from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..input.actions import InputActionsService
from .matcher import ImageMatcherService

MaskArea = tuple[int, int, int, int]


@dataclass(frozen=True)
class CarCardSearchOptions:
    card_path: str
    label: str = "目标车辆"
    required_tag_path: str | None = None
    excluded_tag_path: str | None = None
    required_tag_text: str | None = None
    excluded_tag_text: str | None = None
    exclude_driving: bool = False
    region: tuple[int, int, int, int] | None = None
    final_threshold: float = 0.78
    tag_threshold: float = 0.70
    mask_areas: Sequence[MaskArea] | None = None
    start_page: int = 0
    max_pages: int = 5
    page_step_presses: int = 4
    turn_key: str = "right"
    turn_key_delay: float = 0.06
    turn_pause: float = 0.4


@dataclass(frozen=True)
class CarCardSearchResult:
    position: tuple[int, int]
    page_index: int


@dataclass(frozen=True)
class ConsumableCarCardSearchOptions:
    mastery_card_path: str = "newCC.png"
    remove_card_path: str = "removecarobject.png"
    label: str = "消耗品车辆"
    region: tuple[int, int, int, int] | None = None
    final_threshold: float = 0.78
    tag_threshold: float = 0.70
    mask_areas: Sequence[MaskArea] | None = None
    start_page: int = 0
    max_pages: int = 5
    page_step_presses: int = 4
    turn_key: str = "right"
    turn_key_delay: float = 0.06
    turn_pause: float = 0.4


@dataclass(frozen=True)
class ConsumableCarCardSearchResult:
    action: str
    position: tuple[int, int]
    page_index: int


class CarCardPageSelector:
    """在车辆列表页上执行按页扫描和翻页策略。"""

    def __init__(
        self,
        *,
        image_matcher: ImageMatcherService,
        input_actions: InputActionsService,
        sleep: Callable[[float], None],
        log: Callable[..., None],
    ) -> None:
        self.image_matcher = image_matcher
        self.input_actions = input_actions
        self.sleep = sleep
        self.log = log

    def find(self, options: CarCardSearchOptions) -> CarCardSearchResult | None:
        current_page = max(0, int(options.start_page))
        max_pages = max(1, int(options.max_pages))

        if current_page > 0:
            self.log(f"从第 {current_page} 页开始扫描 {options.label}...", level="debug")
            for _ in range(current_page):
                self._turn_page(options)
                self.sleep(0.15)

        for page_offset in range(max_pages):
            self.log(
                f"扫描{options.label}... (连续未找到: {page_offset}/{max_pages})",
                level="debug",
            )
            pos = self.image_matcher.find_car_card(
                options.card_path,
                required_tag_path=options.required_tag_path,
                excluded_tag_path=options.excluded_tag_path,
                required_tag_text=options.required_tag_text,
                excluded_tag_text=options.excluded_tag_text,
                exclude_driving=options.exclude_driving,
                region=options.region,
                final_threshold=options.final_threshold,
                tag_threshold=options.tag_threshold,
                mask_areas=options.mask_areas,
            )
            if pos:
                self.log(f"锁定{options.label}，当前页码: {current_page}", level="debug")
                return CarCardSearchResult(pos, current_page)

            if page_offset >= max_pages - 1:
                break

            self.log(f"当前页面未找到{options.label}，向右翻页寻找... (第 {page_offset + 1} 次翻页)", level="debug")
            self._turn_page(options)
            self.sleep(options.turn_pause)
            current_page += 1

        return None

    def find_consumable_action(
        self,
        options: ConsumableCarCardSearchOptions,
    ) -> ConsumableCarCardSearchResult | None:
        current_page = max(0, int(options.start_page))
        max_pages = max(1, int(options.max_pages))

        if current_page > 0:
            self.log(f"从第 {current_page} 页开始扫描 {options.label}...", level="debug")
            for _ in range(current_page):
                self._turn_page(options)
                self.sleep(0.15)

        for page_offset in range(max_pages):
            self.log(
                f"扫描{options.label}... (连续未找到: {page_offset}/{max_pages})",
                level="debug",
            )
            match = self.image_matcher.find_consumable_car_card_action(
                mastery_card_path=options.mastery_card_path,
                remove_card_path=options.remove_card_path,
                region=options.region,
                final_threshold=options.final_threshold,
                tag_threshold=options.tag_threshold,
                mask_areas=options.mask_areas,
            )
            if match:
                self.log(f"锁定{options.label}，动作: {match.action}，当前页码: {current_page}", level="debug")
                return ConsumableCarCardSearchResult(match.action, match.position, current_page)

            if page_offset >= max_pages - 1:
                break

            self.log(f"当前页面未找到{options.label}，向右翻页寻找... (第 {page_offset + 1} 次翻页)", level="debug")
            self._turn_page(options)
            self.sleep(options.turn_pause)
            current_page += 1

        return None

    def _turn_page(self, options: CarCardSearchOptions) -> None:
        for _ in range(max(1, int(options.page_step_presses))):
            self.input_actions.hw_press(options.turn_key, delay=options.turn_key_delay)
            self.sleep(0.1)
