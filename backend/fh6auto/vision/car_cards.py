from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import time

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
    fast_mode: bool = True
    candidate_threshold: float = 0.50
    final_threshold: float = 0.78
    title_threshold: float = 0.72
    pi_threshold: float = 0.82
    rarity_threshold: float = 0.68
    body_threshold: float = 0.55
    tag_threshold: float = 0.70
    exclude_tag_threshold: float = 0.65
    max_candidates: int = 80
    mask_areas: Sequence[MaskArea] | None = None
    start_page: int = 0
    max_pages: int = 5
    page_step_presses: int = 4
    page_timeout: float = 1.5
    interval: float = 0.2
    turn_key: str = "right"
    turn_key_delay: float = 0.06
    turn_pause: float = 0.4


@dataclass(frozen=True)
class CarCardSearchResult:
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
            pos = self._find_on_current_page(options)
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

    def _find_on_current_page(self, options: CarCardSearchOptions) -> tuple[int, int] | None:
        deadline = time.monotonic() + max(0.0, options.page_timeout)
        while True:
            pos = self.image_matcher.find_car_card(
                options.card_path,
                required_tag_path=options.required_tag_path,
                excluded_tag_path=options.excluded_tag_path,
                required_tag_text=options.required_tag_text,
                excluded_tag_text=options.excluded_tag_text,
                exclude_driving=options.exclude_driving,
                region=options.region,
                fast_mode=options.fast_mode,
                candidate_threshold=options.candidate_threshold,
                final_threshold=options.final_threshold,
                title_threshold=options.title_threshold,
                pi_threshold=options.pi_threshold,
                rarity_threshold=options.rarity_threshold,
                body_threshold=options.body_threshold,
                tag_threshold=options.tag_threshold,
                exclude_tag_threshold=options.exclude_tag_threshold,
                max_candidates=options.max_candidates,
                mask_areas=options.mask_areas,
            )
            if pos:
                return pos

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sleep(min(options.interval, remaining))

    def _turn_page(self, options: CarCardSearchOptions) -> None:
        for _ in range(max(1, int(options.page_step_presses))):
            self.input_actions.hw_press(options.turn_key, delay=options.turn_key_delay)
            self.sleep(0.1)
