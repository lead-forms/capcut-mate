# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Modified by Hommy <taohongmin@sina.cn> on 2026-06-12
"""剪映自动化控制，主要与自动导出有关"""

import _ctypes
import json
import os
import time
import shutil
import sys

# 平台检查和依赖导入
if sys.platform != "win32":
    raise ImportError("JianyingController is only available on Windows platform")

try:
    import uiautomation as uia
except ImportError as e:
    raise ImportError(f"Missing required Windows dependencies: {e}. Please install with: pip install capcut-mate[windows]")

try:
    import pyautogui  # pyright: ignore[reportMissingModuleSource]
except ImportError as e:
    raise ImportError(f"Missing required Windows dependencies: {e}. Please install with: pip install pyautogui[windows]")

from enum import Enum
from typing import Optional, Literal, Callable

from . import exceptions
from .exceptions import AutomationError

# 添加logger导入
from src.utils.logger import logger

# Windows UI Automation COM 错误（EVENT_E_ALL_SUBSCRIBERS_FAILED）
COM_UIA_ERROR_HRESULT = -2147220991
COM_UIA_ERROR_MARKER = "事件无法调用任何订户"
# UIA 遍历 UI 树时偶发（E_FAIL / 未指定的错误）
COM_E_FAIL_HRESULT = -2147467259
COM_E_FAIL_MARKER = "未指定的错误"
UIA_CLICK_MAX_RETRIES = 4
UIA_CLICK_RETRY_INTERVAL = 1.0


def is_com_uia_error(exc: BaseException) -> bool:
    if isinstance(exc, _ctypes.COMError):
        args = getattr(exc, "args", ())
        if args and args[0] in (COM_UIA_ERROR_HRESULT, COM_E_FAIL_HRESULT):
            return True
        if len(args) >= 2:
            msg = str(args[1])
            if COM_UIA_ERROR_MARKER in msg or COM_E_FAIL_MARKER in msg:
                return True
    text = str(exc)
    return (
        str(COM_UIA_ERROR_HRESULT) in text
        or str(COM_E_FAIL_HRESULT) in text
        or COM_UIA_ERROR_MARKER in text
        or COM_E_FAIL_MARKER in text
    )


class ExportResolution(Enum):
    """导出分辨率"""
    RES_8K = "8K"
    RES_4K = "4K"
    RES_2K = "2K"
    RES_1080P = "1080P"
    RES_720P = "720P"
    RES_480P = "480P"

class ExportFramerate(Enum):
    """导出帧率"""
    FR_24 = "24fps"
    FR_25 = "25fps"
    FR_30 = "30fps"
    FR_50 = "50fps"
    FR_60 = "60fps"

class ControlFinder:
    """控件查找器，封装部分与控件查找相关的逻辑"""

    @staticmethod
    def desc_matcher(target_desc: str, depth: int = 2, exact: bool = False) -> Callable[[uia.Control, int], bool]:
        """根据full_description查找控件的匹配器"""
        target_desc = target_desc.lower()
        def matcher(control: uia.Control, _depth: int) -> bool:
            if _depth != depth:
                return False
            full_desc: str = control.GetPropertyValue(30159).lower()
            return (target_desc == full_desc) if exact else (target_desc in full_desc)
        return matcher

    @staticmethod
    def class_name_matcher(class_name: str, depth: int = 1, exact: bool = False) -> Callable[[uia.Control, int], bool]:
        """根据ClassName查找控件的匹配器"""
        class_name = class_name.lower()
        def matcher(control: uia.Control, _depth: int) -> bool:
            if _depth != depth:
                return False
            curr_class_name: str = control.ClassName.lower()
            return (class_name == curr_class_name) if exact else (class_name in curr_class_name)
        return matcher

class JianyingController:
    """剪映控制器"""

    # 窗口查找重试：剪映启动较慢、RDP 刚连上、或 UI 树尚未就绪时，瞬时 Exists(0) 易失败
    WINDOW_FIND_MAX_RETRIES = 12
    WINDOW_FIND_RETRY_INTERVAL = 1.0

    app: uia.WindowControl
    """剪映窗口"""
    app_status: Literal["home", "edit", "pre_export"]
    """当app_status为pre_export时，app_sub_status表示导出过程中的子状态"""
    app_sub_status: Literal["none", "export_start", "exporting", "export_succeed"]

    def __init__(self):
        """初始化剪映控制器, 此时剪映应该处于目录页"""
        self.get_window()

    def _safe_click(
        self,
        get_control: Callable[[], uia.Control],
        operation: str,
        *,
        exists_timeout: float = 1.0,
        max_retries: int = UIA_CLICK_MAX_RETRIES,
        retry_interval: float = UIA_CLICK_RETRY_INTERVAL,
    ) -> None:
        """带 COM 重试的控件点击；每次尝试重新查找控件，失效时刷新窗口。"""
        last_exc: Optional[BaseException] = None
        for attempt in range(1, max_retries + 1):
            try:
                control = get_control()
                if not control.Exists(exists_timeout, 0.5):
                    raise AutomationError(f"{operation}: control not found")
                control.Click(simulateMove=False)
                return
            except Exception as exc:
                last_exc = exc
                if not is_com_uia_error(exc) or attempt >= max_retries:
                    logger.error(
                        "UIA click failed: operation=%s attempt=%d/%d error=%r",
                        operation,
                        attempt,
                        max_retries,
                        exc,
                        exc_info=not is_com_uia_error(exc),
                    )
                    raise
                logger.warning(
                    "UIA COM error on click, retrying: operation=%s attempt=%d/%d",
                    operation,
                    attempt,
                    max_retries,
                )
                time.sleep(retry_interval)
                self.get_window()
        if last_exc is not None:
            raise last_exc

    def _exists_with_com_retry(
        self,
        control: uia.Control,
        operation: str,
        *,
        timeout: float = 0,
        max_retries: int = UIA_CLICK_MAX_RETRIES,
        retry_interval: float = UIA_CLICK_RETRY_INTERVAL,
        raise_on_exhausted: bool = True,
    ) -> bool:
        """对单个控件的 Exists 调用做 COM 重试；遍历 UI 树时偶发失效元素可由此消化。"""
        search_interval = 0.5 if timeout > 0 else 0
        last_exc: Optional[BaseException] = None
        for attempt in range(1, max_retries + 1):
            try:
                return control.Exists(timeout, search_interval)
            except Exception as exc:
                last_exc = exc
                if not is_com_uia_error(exc) or attempt >= max_retries:
                    logger.error(
                        "UIA Exists failed: operation=%s attempt=%d/%d error=%r",
                        operation,
                        attempt,
                        max_retries,
                        exc,
                        exc_info=not is_com_uia_error(exc),
                    )
                    if raise_on_exhausted:
                        raise
                    return False
                logger.warning(
                    "UIA COM error on Exists, retrying: operation=%s attempt=%d/%d",
                    operation,
                    attempt,
                    max_retries,
                )
                time.sleep(retry_interval)
        if last_exc is not None:
            if raise_on_exhausted:
                raise last_exc
            return False
        return False

    def _safe_exists(
        self,
        get_control: Callable[[], uia.Control],
        operation: str,
        *,
        timeout: float = 0.5,
        max_retries: int = UIA_CLICK_MAX_RETRIES,
        retry_interval: float = UIA_CLICK_RETRY_INTERVAL,
    ) -> bool:
        """带 COM 重试的控件 Exists 检测；每次尝试重新查找控件，失效时刷新窗口。"""
        last_exc: Optional[BaseException] = None
        for attempt in range(1, max_retries + 1):
            try:
                return get_control().Exists(timeout, 0.5)
            except Exception as exc:
                last_exc = exc
                if not is_com_uia_error(exc) or attempt >= max_retries:
                    logger.error(
                        "UIA Exists failed: operation=%s attempt=%d/%d error=%r",
                        operation,
                        attempt,
                        max_retries,
                        exc,
                        exc_info=not is_com_uia_error(exc),
                    )
                    raise
                logger.warning(
                    "UIA COM error on Exists, retrying: operation=%s attempt=%d/%d",
                    operation,
                    attempt,
                    max_retries,
                )
                time.sleep(retry_interval)
                self.get_window()
        if last_exc is not None:
            raise last_exc
        return False

    def _make_export_succeed_close_btn(self, *, from_export_window: bool = False) -> uia.Control:
        root = self.app
        if from_export_window:
            root = self.app.WindowControl(searchDepth=2, Name="导出")
        return root.TextControl(
            searchDepth=2 if from_export_window else 3,
            Compare=ControlFinder.desc_matcher("ExportSucceedCloseBtn"),
        )

    def _find_export_succeed_close_btn(self) -> Optional[uia.Control]:
        """在当前窗口或「导出」子窗口中查找导出成功关闭按钮。"""
        if self._safe_exists(
            lambda: self._make_export_succeed_close_btn(from_export_window=False),
            "find_export_succeed_close_btn.main",
        ):
            return self._make_export_succeed_close_btn(from_export_window=False)

        if self._safe_exists(
            lambda: self.app.WindowControl(searchDepth=2, Name="导出"),
            "find_export_succeed_close_btn.export_window",
        ):
            if self._safe_exists(
                lambda: self._make_export_succeed_close_btn(from_export_window=True),
                "find_export_succeed_close_btn.in_export_window",
            ):
                return self._make_export_succeed_close_btn(from_export_window=True)
        return None

    def _require_export_succeed_close_btn(self) -> uia.Control:
        btn = self._find_export_succeed_close_btn()
        if btn is None:
            raise AutomationError("export succeed close button not found")
        return btn

    def _dismiss_export_success_dialog(self) -> bool:
        """关闭导出成功弹窗；返回是否找到并点击了关闭按钮。"""
        try:
            close_btn = self._find_export_succeed_close_btn()
        except Exception as exc:
            if is_com_uia_error(exc):
                logger.warning(
                    "COM error while locating export success close button: %r",
                    exc,
                )
                self.get_window()
                return False
            raise
        if close_btn is None:
            return False
        logger.info("Dismissing export success dialog")
        self._safe_click(
            self._require_export_succeed_close_btn,
            "dismiss_export_success_dialog",
        )
        time.sleep(2)
        self.get_window()
        return True

    def find_and_click_draft(
        self,
        draft_name: str,
        max_retries: int = 6,
        retry_interval: float = 5.0,
        draft_dir: Optional[str] = None,
    ) -> None:
        """查找并点击指定名称的草稿
        
        Args:
            draft_name (str): 要查找的草稿名称
            max_retries (int): 最大重试次数，默认6次
            retry_interval (float): 重试间隔时间(秒)，默认5秒
            draft_dir (str, optional): 剪映本地草稿目录；未找到时会触发 robocopy 扫描以刷新列表
            
        Raises:
            DraftNotFound: 未找到指定名称的剪映草稿
        """
        last_exception = None
        for attempt in range(max_retries):
            try:
                # 点击对应草稿
                draft_name_text = self.app.TextControl(
                    searchDepth=2,
                    Compare=self._draft_title_matcher(draft_name),
                )
                if not draft_name_text.Exists(0):
                    if draft_dir and self._click_draft_card_from_root_meta(draft_name, draft_dir):
                        time.sleep(10)
                        self.get_window()
                        if self.app_status == "edit":
                            return
                    raise exceptions.DraftNotFound(f"未找到名为{draft_name}的剪映草稿")
                draft_btn = draft_name_text.GetParentControl()
                assert draft_btn is not None
                draft_btn.Click(simulateMove=False)
                time.sleep(10)
                self.get_window()
                return  # 成功则返回
            except exceptions.DraftNotFound as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.info(
                        "Draft not found (name=%s), retry %d/%d",
                        draft_name,
                        attempt + 1,
                        max_retries,
                    )
                    if draft_dir and os.path.isdir(draft_dir):
                        from src.utils.draft_downloader import trigger_directory_scan_with_robocopy
                        logger.info(
                            "Triggering robocopy directory scan before retry: %s",
                            draft_dir,
                        )
                        trigger_directory_scan_with_robocopy(draft_dir)
                    time.sleep(retry_interval)
        
        # 所有重试都失败，抛出异常
        raise last_exception

    def _click_draft_card_from_root_meta(self, draft_name: str, draft_dir: str) -> bool:
        """Open a Jianying 11 card when QML does not expose its text to UIA."""
        root_dir = os.path.dirname(os.path.normpath(draft_dir))
        meta_path = os.path.join(root_dir, "root_meta_info.json")
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                stores = json.load(handle).get("all_draft_store", [])
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            logger.warning("Cannot read Jianying root metadata for card fallback: %s", exc)
            return False

        visible = [
            item for item in stores
            if isinstance(item, dict)
            and not item.get("draft_is_invisible", False)
            and os.path.isdir(str(item.get("draft_fold_path") or ""))
            and not str(item.get("draft_name") or "").endswith(".tmp")
        ]
        visible.sort(key=lambda item: int(item.get("tm_draft_modified") or 0), reverse=True)
        index = next(
            (i for i, item in enumerate(visible) if item.get("draft_name") == draft_name),
            None,
        )
        if index is None:
            logger.warning("Draft is absent from Jianying root metadata: %s", draft_name)
            return False

        rect = self.app.BoundingRectangle
        card_width = 112
        columns = max(1, (int(rect.right - rect.left) - 240) // card_width)
        x = int(rect.left) + 295 + (index % columns) * card_width
        # Use the top edge: Jianying's UIA bottom coordinate is DPI-scaled on
        # 125% Windows displays while pyautogui consumes logical coordinates.
        # The first row is stable at y=605 in the window client area.
        y = int(rect.top) + 605 + (index // columns) * 140
        logger.warning(
            "Draft title unavailable in UIA tree; clicking indexed home card: "
            "name=%s index=%d columns=%d x=%d y=%d",
            draft_name,
            index,
            columns,
            x,
            y,
        )
        pyautogui.click(x=x, y=y, button="left")
        return True

    @staticmethod
    def _draft_title_matcher(draft_name: str) -> Callable[[uia.Control, int], bool]:
        """Match full and Jianying 11 ellipsized draft-card descriptions."""
        prefix = "HomePageDraftTitle:"

        def matcher(control: uia.Control, depth: int) -> bool:
            if depth != 2:
                return False
            description = str(control.GetPropertyValue(30159) or "")
            if not description.startswith(prefix):
                return False
            visible_name = description[len(prefix):]
            if visible_name == draft_name:
                return True
            if "..." not in visible_name:
                return False
            visible_prefix, visible_suffix = visible_name.split("...", 1)
            return draft_name.startswith(visible_prefix) and draft_name.endswith(visible_suffix)

        return matcher

    def click_export_button(self) -> None:
        """点击编辑页面的导出按钮
        
        Raises:
            AutomationError: 未找到导出按钮
        """
        export_btn = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("MainWindowTitleBarExportBtn"))
        if not export_btn.Exists(0):
            # Jianying 11 exposes the title-bar button itself with this stable
            # automation id even when the nested text node is not yet ready.
            export_btn = self.app.ButtonControl(
                searchDepth=1,
                AutomationId="editor.export",
            )
        if not export_btn.Exists(2, 0.5):
            # Jianying 11's QML accessibility subtree is occasionally absent.
            # The export button remains anchored to the top-right of MainWindow.
            logger.warning("Export button missing from UIA tree; using window-relative click")
            screen_width, _ = pyautogui.size()
            pyautogui.click(x=screen_width - 135, y=18, button="left")
        else:
            export_btn.Click(simulateMove=False)
        time.sleep(10)
        self.get_window()

    def get_original_export_path(self) -> str:
        """获取原始导出路径
        
        Returns:
            str: 原始导出路径
            
        Raises:
            AutomationError: 未找到导出路径框
        """
        # 获取原始导出路径（带后缀名）
        export_path_sib = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportPath"))
        if not export_path_sib.Exists(0):
            draft_name = getattr(self, "_current_draft_name", "")
            if not draft_name:
                raise AutomationError("未找到导出路径框")
            export_path = os.path.join(os.path.expanduser("~/Videos"), f"{draft_name}.mp4")
            logger.warning("Export path missing from UIA tree; using Jianying default: %s", export_path)
            self._expected_export_path = export_path
            return export_path
        export_path_text = export_path_sib.GetSiblingControl(lambda ctrl: True)
        assert export_path_text is not None
        export_path = export_path_text.GetPropertyValue(30159)
        self._expected_export_path = export_path
        return export_path

    def set_export_resolution(self, resolution: Optional[ExportResolution]) -> None:
        """设置导出分辨率
        
        Args:
            resolution (Optional[ExportResolution]): 导出分辨率，如果为None则不设置
            
        Raises:
            AutomationError: 未找到相关控件
        """
        if resolution is not None:
            setting_group = self.app.GroupControl(searchDepth=1,
                                          Compare=ControlFinder.class_name_matcher("PanelSettingsGroup_QMLTYPE"))
            if not setting_group.Exists(0):
                raise AutomationError("未找到导出设置组")
            resolution_btn = setting_group.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportSharpnessInput"))
            if not resolution_btn.Exists(0.5):
                raise AutomationError("未找到导出分辨率下拉框")
            resolution_btn.Click(simulateMove=False)
            time.sleep(0.5)
            resolution_item = self.app.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher(resolution.value)
            )
            if not resolution_item.Exists(0.5):
                raise AutomationError(f"未找到{resolution.value}分辨率选项")
            resolution_item.Click(simulateMove=False)
            time.sleep(0.5)

    def set_export_framerate(self, framerate: Optional[ExportFramerate]) -> None:
        """设置导出帧率
        
        Args:
            framerate (Optional[ExportFramerate]): 导出帧率，如果为None则不设置
            
        Raises:
            AutomationError: 未找到相关控件
        """
        if framerate is not None:
            setting_group = self.app.GroupControl(searchDepth=1,
                                          Compare=ControlFinder.class_name_matcher("PanelSettingsGroup_QMLTYPE"))
            if not setting_group.Exists(0):
                raise AutomationError("未找到导出设置组")
            framerate_btn = setting_group.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("FrameRateInput"))
            if not framerate_btn.Exists(0.5):
                raise AutomationError("未找到导出帧率下拉框")
            framerate_btn.Click(simulateMove=False)
            time.sleep(0.5)
            framerate_item = self.app.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher(framerate.value)
            )
            if not framerate_item.Exists(0.5):
                raise AutomationError(f"未找到{framerate.value}帧率选项")
            framerate_item.Click(simulateMove=False)
            time.sleep(0.5)

    def click_final_export_button(self) -> None:
        """点击导出窗口的最终导出按钮
        
        Raises:
            AutomationError: 未找到导出按钮
        """
        export_btn = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportOkBtn", exact=True))
        if not export_btn.Exists(0):
            # Jianying 11 export dialog is a child QML window. Its primary
            # action is consistently anchored at bottom-right.
            rect = self.app.BoundingRectangle
            logger.warning("Final export button missing from UIA tree; using dialog-relative click")
            pyautogui.click(x=int(rect.right) - 130, y=int(rect.bottom) - 27, button="left")
        else:
            export_btn.Click(simulateMove=False)
        time.sleep(5)

    def __ensure_window_focus(self) -> None:
        """在点击前确保窗口有焦点"""
        # 1. 确保窗口激活
        self.app.SetActive()
        time.sleep(1)
        
        # 2. 确保窗口置顶
        self.app.SetTopmost()
        time.sleep(1)
        
        # 3. 强制获取焦点
        try:
            self.app.SetFocus()
        except:
            pass  # 某些情况下可能失败，但继续执行
        time.sleep(1)

    def wait_for_export_completion(self, timeout: float) -> bool:
        """等待导出完成
        
        Args:
            timeout (float): 超时时间（秒）
            
        Returns:
            bool: 是否已关闭导出成功弹窗（表示导出已完成）
            
        Raises:
            AutomationError: 导出超时
        """
        # 点击继续导出按钮次数
        continue_export_click_count = 0
        export_succeeded = False

        # 等待导出完成
        st = time.time()
        while True:
            self.get_window()
            expected_path = getattr(self, "_expected_export_path", "")
            if expected_path and os.path.isfile(expected_path):
                logger.info("Export output exists, treating render as complete: %s", expected_path)
                # Jianying 11 may expose the success page as "exporting" and
                # omit its close button from UIA. Close the bottom-right action
                # so the next render is not left behind a modal.
                if self.app_status == "pre_export":
                    rect = self.app.BoundingRectangle
                    pyautogui.click(
                        x=int(rect.right) - 52,
                        y=int(rect.bottom) - 27,
                        button="left",
                    )
                    time.sleep(1)
                export_succeeded = True
                break
            if self.app_status != "pre_export":
                break

            if self._find_export_succeed_close_btn() is not None:
                logger.info("Export finished, closing success dialog")
                self._safe_click(
                    self._require_export_succeed_close_btn,
                    "wait_for_export_completion.close_success",
                )
                time.sleep(2)
                export_succeeded = True
                break

            if time.time() - st > timeout:
                raise AutomationError("导出超时, 时限为%d秒" % timeout)

            # 导出过程中，如果出现异常弹窗，则点击继续导出按钮
            if continue_export_click_count < 20:
                print("pyautogui.size(): ", pyautogui.size(), ", click index: ", continue_export_click_count)
                pyautogui.click(x=996, y=597, button="left")
                continue_export_click_count += 1

            time.sleep(1)
        time.sleep(2)
        return export_succeeded

    def return_to_home(self) -> None:
        """回到目录页并稍作延迟"""
        self.get_window()
        self._dismiss_export_success_dialog()
        self.switch_to_home()
        time.sleep(2)

    def move_exported_file(self, original_path: str, output_path: Optional[str]) -> None:
        """移动导出的文件到指定位置
        
        Args:
            original_path (str): 原始导出路径
            output_path (Optional[str]): 目标输出路径，如果为None则不移动
        """
        logger.info(f"move {original_path} to {output_path}")
        if output_path is not None:
            if not original_path:
                raise AutomationError("导出完成但未解析到原始输出路径")
            shutil.move(original_path, output_path)

    def export_draft(self, draft_name: str, output_path: Optional[str] = None, *,
                     resolution: Optional[ExportResolution] = None,
                     framerate: Optional[ExportFramerate] = None,
                     timeout: float = 300,
                     draft_dir: Optional[str] = None) -> None:
        """导出指定的剪映草稿, **目前仅支持剪映6及以下版本**

        **注意: 需要确认有导出草稿的权限(不使用VIP功能或已开通VIP), 否则可能陷入死循环**

        Args:
            draft_name (`str`): 要导出的剪映草稿名称
            output_path (`str`, optional): 导出路径, 支持指向文件夹或直接指向文件, 不指定则使用剪映默认路径.
            resolution (`Export_resolution`, optional): 导出分辨率, 默认不改变剪映导出窗口中的设置.
            framerate (`Export_framerate`, optional): 导出帧率, 默认不改变剪映导出窗口中的设置.
            timeout (`float`, optional): 导出超时时间(秒), 默认为5分钟.
            draft_dir (`str`, optional): 剪映本地草稿目录；未在首页找到草稿时会 robocopy 触发扫描后重试.

        Raises:
            `DraftNotFound`: 未找到指定名称的剪映草稿
            `AutomationError`: 剪映操作失败
        """
        logger.info(f"start export {draft_name} to {output_path}")
        self._current_draft_name = draft_name

        # 初始化准备
        self.get_window()
        self.switch_to_home()

        original_path = None
        export_completed = False

        for i in range(16):
            # 确保窗口有焦点
            self.__ensure_window_focus()
            if self.app_status == "home":
                logger.info("[%d]app is already in home page", i)
                self.find_and_click_draft(draft_name, draft_dir=draft_dir)
            elif self.app_status == "edit":
                if export_completed or (
                    original_path and os.path.isfile(original_path)
                ):
                    logger.info(
                        "[%d]export already finished, skip re-export and return home",
                        i,
                    )
                    self.return_to_home()
                    break
                logger.info("[%d]app is already in edit page", i)
                # 点击导出按钮进入导出界面
                self.click_export_button()
            elif self.app_status == "pre_export":                
                if self.app_sub_status == "export_start":
                    logger.info("[%d]app is already in pre_export[export_start] page", i)
                    # 获取原始导出路径
                    original_path = self.get_original_export_path()
                    # 设置分辨率（如果指定）
                    self.set_export_resolution(resolution)                    
                    # 设置帧率（如果指定）
                    self.set_export_framerate(framerate)                    
                    # 点击最终导出按钮
                    self.click_final_export_button()
                    # 获取窗口状态
                    self.get_window()
                elif self.app_sub_status == "exporting":
                    logger.info("[%d]app is already in pre_export[exporting] page", i)
                    if original_path is None:
                        # Jianying 11 can hide ExportOkBtn from UIA, causing
                        # its settings page to be classified as "exporting".
                        logger.warning(
                            "Pre-export controls unavailable; treating first "
                            "exporting state as Jianying 11 export settings"
                        )
                        original_path = self.get_original_export_path()
                        self.set_export_resolution(resolution)
                        self.set_export_framerate(framerate)
                        self.click_final_export_button()
                        self.get_window()
                        continue
                    if self.wait_for_export_completion(timeout):
                        export_completed = True
                        self.return_to_home()
                        break
                    self.get_window()
                    if original_path and os.path.isfile(original_path):
                        logger.info(
                            "[%d]export output file exists after wait, treating as success",
                            i,
                        )
                        export_completed = True
                        self.return_to_home()
                        break
                elif self.app_sub_status == "export_succeed":
                    logger.info("[%d]app is already in pre_export[export_succeed] page", i)
                    export_completed = True
                    self.return_to_home()
                    break
                else:
                    raise AutomationError("[%d]app is in unknown sub-status: %s" % (i, self.app_sub_status))
            else:
                raise AutomationError("[%d]app is in unknown status: %s" % (i, self.app_status))
        
        # 移动导出文件到指定路径（如果指定）
        self.move_exported_file(original_path, output_path)
        
        logger.info(f"export {draft_name} to {output_path} completed")

    def switch_to_home(self) -> None:
        """切换到剪映主页"""
        for i in range(8):
            self.get_window()
            if self.app_status == "home":
                return

            if self._dismiss_export_success_dialog():
                continue

            if self.app_status == "pre_export":
                # 导出弹窗未识别为 export_succeed 时，仍尝试关闭成功页或按 ESC 退出
                if self.app_sub_status in ("export_succeed", "exporting", "export_start"):
                    if self._find_export_succeed_close_btn() is not None:
                        self._safe_click(
                            self._require_export_succeed_close_btn,
                            f"switch_to_home.pre_export_close[{i}]",
                        )
                        time.sleep(2)
                        continue
                if self.app_sub_status == "export_start":
                    logger.info("Closing Jianying export-start dialog with Escape")
                    self.app.SetActive()
                    pyautogui.press("esc")
                    time.sleep(2)
                    continue
                logger.warning(
                    "switch_to_home: stuck in pre_export sub_status=%s, attempt=%d",
                    self.app_sub_status,
                    i,
                )
                time.sleep(1)
                continue

            if self.app_status == "edit":
                close_btn = self.app.GroupControl(
                    searchDepth=1,
                    ClassName="TitleBarButton",
                    foundIndex=3,
                )
                if not close_btn.Exists(1, 0.5):
                    logger.warning(
                        "switch_to_home: edit close button missing, attempt=%d",
                        i,
                    )
                    time.sleep(1)
                    continue
                self._safe_click(
                    lambda: self.app.GroupControl(
                        searchDepth=1,
                        ClassName="TitleBarButton",
                        foundIndex=3,
                    ),
                    f"switch_to_home.edit_close[{i}]",
                )
                time.sleep(2)
                continue

            raise AutomationError("invalid app status: %s" % self.app_status)

        logger.warning("Cannot switch to home page after %d attempts", 8)

    def get_window(
        self,
        max_retries: Optional[int] = None,
        retry_interval: Optional[float] = None,
    ) -> None:
        """寻找剪映窗口并置顶；未找到时按间隔重试以提高容错。"""
        if max_retries is None:
            max_retries = self.WINDOW_FIND_MAX_RETRIES
        if retry_interval is None:
            retry_interval = self.WINDOW_FIND_RETRY_INTERVAL

        if hasattr(self, "app"):
            try:
                if self._exists_with_com_retry(
                    self.app,
                    "get_window.clear_topmost",
                    timeout=0,
                    raise_on_exhausted=False,
                ):
                    self.app.SetTopmost(False)
            except Exception as exc:
                if not is_com_uia_error(exc):
                    raise
                logger.warning(
                    "Stale Jianying window handle when clearing topmost: %r",
                    exc,
                )

        for attempt in range(max_retries):
            self.app = uia.WindowControl(searchDepth=1, Compare=self.__jianying_window_cmp)
            if self._exists_with_com_retry(
                self.app,
                "get_window.find_main",
                timeout=0,
                raise_on_exhausted=False,
            ):
                if attempt > 0:
                    logger.info(
                        "Jianying main window matched on attempt %d/%d",
                        attempt + 1,
                        max_retries,
                    )
                break
            if attempt < max_retries - 1:
                logger.warning(
                    "Jianying main window not found, retrying in %.1fs (%d/%d)",
                    retry_interval,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(retry_interval)
        else:
            raise AutomationError(
                "Jianying window not found after %d attempts (%.1fs interval); "
                "ensure Jianying Pro is open on the home or edit screen."
                % (max_retries, retry_interval)
            )

        # Jianying 11 renders the export UI as an overlay inside MainWindow,
        # while older releases expose a child Window named "导出".
        if self._main_window_has_export_overlay():
            self.app_status = "pre_export"
        else:
            export_window = self.app.WindowControl(
                searchDepth=1,
                Compare=self._export_window_cmp,
            )
            if self._exists_with_com_retry(
                export_window,
                "get_window.find_export",
                timeout=0,
                raise_on_exhausted=False,
            ):
                self.app = export_window
                self.app_status = "pre_export"

        # 初始化导出子状态
        self.init_export_sub_status()

        logger.info("app_status: %s, app_sub_status: %s", self.app_status, self.app_sub_status)

        self.app.SetActive()
        self.app.SetTopmost()

    def _main_window_has_export_overlay(self) -> bool:
        """Return whether a Jianying 11-style inline export overlay is open."""
        markers = (
            ("ExportOkBtn", 2, True),
            ("ExportPath", 2, False),
            ("ExportSucceedCloseBtn", 3, False),
        )
        for marker, depth, exact in markers:
            control = self.app.TextControl(
                searchDepth=depth,
                Compare=ControlFinder.desc_matcher(marker, depth=depth, exact=exact),
            )
            if self._exists_with_com_retry(
                control,
                f"get_window.find_inline_export.{marker}",
                timeout=0,
                raise_on_exhausted=False,
            ):
                return True
        return False

    @staticmethod
    def _export_window_cmp(control: uia.Control, depth: int) -> bool:
        """Match legacy ``导出`` and Jianying 11 ``导出-<draft>`` windows."""
        if depth != 1:
            return False
        try:
            name = (control.Name or "").strip()
            class_name = (control.ClassName or "").lower()
        except Exception as exc:
            if is_com_uia_error(exc):
                return False
            raise
        return name == "导出" or name.startswith("导出-") or "exportwindow" in class_name

    # 初始化导出子状态
    def init_export_sub_status(self) -> None:
        if self.app_status == "pre_export":
            # 0. 初始化默认值为导出中
            self.app_sub_status = "exporting"
            
            # 1. 检查窗口是否停留在导出开始页面
            export_ok_btn = self.app.TextControl(searchDepth=2, Compare=ControlFinder.desc_matcher("ExportOkBtn", exact=True))
            if export_ok_btn.Exists(0):
                self.app_sub_status = "export_start"
                return

            # 2. 检查窗口是否停留在导出完成页面
            if self._safe_exists(
                lambda: self._make_export_succeed_close_btn(from_export_window=False),
                "init_export_sub_status.export_succeed",
                timeout=0,
            ):
                self.app_sub_status = "export_succeed"
                return
        else:
            self.app_sub_status = "none"

    def __jianying_window_cmp(self, control: uia.WindowControl, depth: int) -> bool:
        try:
            name = control.Name
        except Exception as exc:
            if is_com_uia_error(exc):
                return False
            raise
        if name != "剪映专业版":
            return False
        try:
            class_name = control.ClassName
        except Exception as exc:
            if is_com_uia_error(exc):
                return False
            raise
        class_name_lower = class_name.lower()
        if "homepage" in class_name_lower:
            self.app_status = "home"
            return True
        if "mainwindow" in class_name_lower:
            self.app_status = "edit"
            return True

        logger.info("ClassName: %s, Name: %s", class_name_lower, name.lower())
        return False
