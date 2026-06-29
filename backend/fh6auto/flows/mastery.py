from __future__ import annotations
from collections.abc import Callable

from ..recovery import RecoveryService
from ..window import GameWindowService
from ..backend.config_service import BackendConfigService
from ..backend.state import RuntimeState
from ..input.actions import InputActionsService
from ..vision.car_cards import CarCardPageSelector, ConsumableCarCardSearchOptions
from ..vision.manufacturer import ManufacturerDetector
from ..vision.matcher import ImageMatcherService
from ..vision.player_stats import PlayerStatsDetector
from ..vision.polling import ImageWaitsService


class MasteryFlow:
    NOT_FOUND_PAGE_LIMIT = 5
    SKILL_GRID_SIZE = 4
    SKILL_START_CELL = 12

    def __init__(
        self,
        *,
        state: RuntimeState,
        config: BackendConfigService,
        game_window: GameWindowService,
        input_actions: InputActionsService,
        image_matcher: ImageMatcherService,
        image_waits: ImageWaitsService,
        manufacturer: ManufacturerDetector,
        player_stats: PlayerStatsDetector,
        recovery: RecoveryService,
        sleep: Callable[[float], None],
        log: Callable[..., None],
    ) -> None:
        self.state = state
        self.config = config
        self.game_window = game_window
        self.input_actions = input_actions
        self.image_matcher = image_matcher
        self.image_waits = image_waits
        self.manufacturer = manufacturer
        self.player_stats = player_stats
        self.recovery = recovery
        self.sleep = sleep
        self.log = log
        self.car_cards = CarCardPageSelector(
            image_matcher=self.image_matcher,
            input_actions=self.input_actions,
            sleep=self.sleep,
            log=self.log,
        )
        self._skill_order_cache_key: tuple[int, ...] | None = None
        self._skill_order_cache: list[int] = []

    def _skill_points_per_car(self) -> int:
        raw_points = self.config.values.get("calc_c", "30")
        digits = "".join(ch for ch in str(raw_points) if ch.isdigit())
        try:
            points = int(digits)
        except Exception:
            points = 30
        return max(1, points)

    def _check_no_skill_points(self):
        pos = self.image_matcher.find_image_sift(
            "SPNE.png",
            min_inliers=24,
        )
        if pos:
            self.log("检测到技能点不足，准备提前结束熟练度加点。", level="debug")
            sleep = self.sleep
            self.input_actions.hw_press("enter")
            sleep(0.8)
            for _ in range(3):
                self.input_actions.hw_press("esc")
                sleep(1.0)
            return True
        else:
            return False

    @classmethod
    def _skill_cell_position(cls, cell: int) -> tuple[int, int]:
        return divmod(cell, cls.SKILL_GRID_SIZE)

    @classmethod
    def _skill_cell_distance(cls, left: int, right: int) -> int:
        left_row, left_col = cls._skill_cell_position(left)
        right_row, right_col = cls._skill_cell_position(right)
        return abs(left_row - right_row) + abs(left_col - right_col)

    @classmethod
    def _is_adjacent_skill_cell(cls, left: int, right: int) -> bool:
        return cls._skill_cell_distance(left, right) == 1

    def _skill_cells(self) -> list[int]:
        raw_cells = self.config.values.get("skill_cells", [])
        if not isinstance(raw_cells, list):
            return []

        cells: list[int] = []
        seen: set[int] = set()
        for item in raw_cells:
            try:
                cell = int(item)
            except Exception:
                continue
            if not (0 <= cell < self.SKILL_GRID_SIZE * self.SKILL_GRID_SIZE):
                continue
            if cell == self.SKILL_START_CELL or cell in seen:
                continue
            seen.add(cell)
            cells.append(cell)

        connected = {self.SKILL_START_CELL}
        remaining = set(cells)
        while True:
            added = {
                cell
                for cell in remaining
                if any(self._is_adjacent_skill_cell(cell, connected_cell) for connected_cell in connected)
            }
            if not added:
                break
            connected.update(added)
            remaining.difference_update(added)

        return sorted(cell for cell in cells if cell in connected)

    def _shortest_skill_cell_order(self, cells: list[int]) -> list[int]:
        if not cells:
            return []

        total_cells = len(cells)
        start_slot = total_cells
        full_mask = (1 << total_cells) - 1
        infinity = 10**9
        dp: dict[tuple[int, int], int] = {(0, start_slot): 0}
        parent: dict[tuple[int, int], tuple[int, int]] = {}

        for mask in range(1 << total_cells):
            purchased_cells = [self.SKILL_START_CELL] + [
                cell for index, cell in enumerate(cells) if mask & (1 << index)
            ]
            for last_slot in range(total_cells + 1):
                current_cost = dp.get((mask, last_slot))
                if current_cost is None:
                    continue

                current_cell = self.SKILL_START_CELL if last_slot == start_slot else cells[last_slot]
                for next_slot, next_cell in enumerate(cells):
                    if mask & (1 << next_slot):
                        continue
                    if not any(self._is_adjacent_skill_cell(next_cell, purchased) for purchased in purchased_cells):
                        continue

                    next_mask = mask | (1 << next_slot)
                    next_state = (next_mask, next_slot)
                    next_cost = current_cost + self._skill_cell_distance(current_cell, next_cell)
                    if next_cost >= dp.get(next_state, infinity):
                        continue

                    dp[next_state] = next_cost
                    parent[next_state] = (mask, last_slot)

        end_state = min(
            ((full_mask, last_slot) for last_slot in range(total_cells)),
            key=lambda state: dp.get(state, infinity),
            default=None,
        )
        if end_state is None or dp.get(end_state, infinity) >= infinity:
            return []

        order: list[int] = []
        state = end_state
        while state != (0, start_slot):
            _, last_slot = state
            order.append(cells[last_slot])
            state = parent[state]
        order.reverse()
        return order

    def _cached_skill_cell_order(self, cells: list[int]) -> tuple[list[int], bool]:
        cache_key = tuple(cells)
        if self._skill_order_cache_key == cache_key:
            return list(self._skill_order_cache), False

        order = self._shortest_skill_cell_order(cells)
        self._skill_order_cache_key = cache_key
        self._skill_order_cache = list(order)
        return order, True

    def _movement_keys_between_skill_cells(self, source: int, target: int) -> list[str]:
        source_row, source_col = self._skill_cell_position(source)
        target_row, target_col = self._skill_cell_position(target)
        keys: list[str] = []

        vertical_key = "up" if target_row < source_row else "down"
        keys.extend([vertical_key] * abs(target_row - source_row))

        horizontal_key = "left" if target_col < source_col else "right"
        keys.extend([horizontal_key] * abs(target_col - source_col))
        return keys

    def _enter_consumable_manufacturer_list(self) -> bool:
        sleep = self.sleep
        self.log("进入我的车辆.", level="debug")
        self.input_actions.hw_press("enter")
        sleep(2.0)

        self.input_actions.hw_press("y")
        sleep(0.5)
        for _ in range(2):
            self.input_actions.hw_press("down")
            sleep(0.1)
        self.input_actions.hw_press("enter")
        sleep(0.1)
        self.input_actions.hw_press("esc")
        sleep(0.5)

        self.input_actions.hw_press("backspace")
        sleep(0.5)

        manufacturer_pos = self.manufacturer.scan_for_text("斯巴鲁", threshold=0.75, label="消耗品制造商")
        if not manufacturer_pos:
            self.log("选择制造商失败", level="warning")
            return False

        self.input_actions.game_click(manufacturer_pos)
        sleep(1.0)
        return True

    def _remove_selected_consumable_car(self) -> bool:
        sleep = self.sleep
        self.log("删除符合条件的消耗品车辆...", level="debug")
        self.input_actions.move_to_game_coord(5, 5)
        sleep(0.5)

        # 若点击前车辆已选中，会直接弹出“选择操作”菜单；否则需要 Enter 打开菜单。
        pos_cancel = self.image_waits.wait_for_footer_text_ui("取消", timeout=1.0, interval=0.2)
        if not pos_cancel:
            self.input_actions.hw_press("enter")
            sleep(0.5)

        for _ in range(4):
            self.input_actions.hw_press("down")
            sleep(0.1)
        self.input_actions.hw_press("enter")
        sleep(0.5)

        self.input_actions.hw_press("down")
        sleep(0.1)
        self.input_actions.hw_press("enter")
        sleep(1.0)

        self.state.sc_count += 1
        self.log(f"已移除消耗品车辆，累计移除 {self.state.sc_count} 辆。", level="debug")
        return True

    def _drive_selected_car_and_apply_mastery(self, effective_target: int) -> str | None:
        sleep = self.sleep
        self.log("确认上车并驾驶当前车辆...", level="debug")
        self.input_actions.hw_press("enter")
        sleep(1.0)
        self.input_actions.hw_press("enter")

        sleep(10.0)
        pos_drive = self.image_waits.wait_for_footer_text_ui("驾驶")
        if not pos_drive:
            self.log("上新车后的检视，底部未找到“驾驶”", level="warning")
            return "error"

        # 退出新车检视界面，返回车辆菜单
        self.input_actions.hw_press("esc")
        sleep(1.5)

        # 点击 升级与调校
        self.input_actions.hw_press("down")
        sleep(0.1)
        self.input_actions.hw_press("enter")
        sleep(1.0)

        # 点击 车辆专精
        for _ in range(7):
            self.input_actions.hw_press("down")
            sleep(0.1)
        self.input_actions.hw_press("enter")
        sleep(1.0)

        skill_cells = self._skill_cells()
        skill_order, skill_order_recalculated = self._cached_skill_cell_order(skill_cells)
        if skill_order_recalculated and len(skill_order) < len(skill_cells):
            self.log("技能路径中存在无法连通的格子，已跳过无法规划的部分。", level="warning")
        if skill_order_recalculated:
            self.log(f"技能加点位置: {skill_cells}，最短执行顺序: {skill_order}", level="debug")

        self.input_actions.hw_press("enter")
        sleep(1.2)
        if self._check_no_skill_points():
            return "技能点不足"

        current_cell = self.SKILL_START_CELL
        for target_cell in skill_order:
            for key in self._movement_keys_between_skill_cells(current_cell, target_cell):
                self.input_actions.hw_press(key)
                sleep(0.2)
            self.input_actions.hw_press("enter")
            sleep(1.2)
            if self._check_no_skill_points():
                return "技能点不足"
            current_cell = target_cell

        self.state.mastery_counter += 1
        self.state.set_task("加点&删车", self.state.mastery_counter, effective_target)

        self.input_actions.hw_press("esc")
        sleep(1.2)
        self.input_actions.hw_press("esc")
        sleep(0.8)
        self.input_actions.hw_press("up", delay=0.15)
        sleep(0.8)
        return None

    # ==========================================
    # --- 模块：熟练度加点 ---
    # ==========================================
    def logic_mastery(self, target_count, *, use_all: bool = False):
        target_count = max(0, int(target_count))
        use_all = bool(use_all)
        sleep = self.sleep
        start_count = self.state.mastery_counter
        start_remove_count = self.state.sc_count

        def finish(reason: str | None = None) -> bool:
            suffix = f"原因：{reason}" if reason else ""
            removed_count = self.state.sc_count - start_remove_count
            self.log(f"熟练度加点流程结束：完成 {self.state.mastery_counter - start_count} 次，移除 {removed_count} 辆。{suffix}")
            return True

        if not use_all and self.state.mastery_counter >= target_count:
            return finish()

        if use_all:
            self.state.set_task("熟练度加点")
        else:
            self.state.set_task("熟练度加点", self.state.mastery_counter, target_count)
        self.log("准备验证/进入菜单...", level="debug")
        if not self.recovery.enter_menu():
            return False

        self.log("进入车辆与收藏...", level="debug")
        self.input_actions.hw_press("pagedown", delay=0.15)
        sleep(1.0)

        available_skill_points = self.player_stats.find_current_skill_points_value()
        if available_skill_points is None:
            self.log("熟练度加点：未能通过 OCR 识别当前技术点数，无法计算动态加点数量。", level="warning")
            return False

        points_per_car = self._skill_points_per_car()
        remaining_user_count = max(0, target_count - self.state.mastery_counter)
        affordable_count = available_skill_points // points_per_car
        planned_count = affordable_count if use_all else min(remaining_user_count, affordable_count)
        effective_target = self.state.mastery_counter + planned_count
        target_text = "目标模式：用完可用技术点" if use_all else f"用户剩余目标 {remaining_user_count} 辆"
        self.log(
            f"熟练度加点：当前技术点 {available_skill_points}，单车消耗 {points_per_car} 点，"
            f"{target_text}，动态最多可加点 {affordable_count} 辆，预计处理 {planned_count} 辆。"
        )

        if planned_count <= 0:
            reason = "当前技术点不足以完成一辆车加点" if affordable_count <= 0 else "执行次数为 0"
            return finish(reason)

        self.state.set_task("熟练度加点", self.state.mastery_counter, effective_target)

        pos_buycar = self.image_waits.wait_for_image_sift(
            "buy_new_used_cars.png",
            region=self.game_window.regions["左"],
            min_inliers=20,
            timeout=15,
            interval=0.3,
        )
        if not pos_buycar:
            self.log("未识别到 购买新车与二手车", level="warning")
            return False

        self.input_actions.game_click(pos_buycar)
        sleep(0.8)
        self.input_actions.hw_press("enter")
        sleep(2)

        pos_bs = self.image_waits.wait_for_footer_text_ui("选择")
        if not pos_bs:
            self.log("未找到 选择", level="warning")
            return False

        self.input_actions.hw_press("pagedown", delay=0.15)
        self.log("进入车辆界面...", level="debug")
        sleep(0.5)

        current_page_position = max(0, int(self.state.memory_car_page or 0))
        list_needs_reset = True

        while self.state.mastery_counter < effective_target:
            if list_needs_reset and not self._enter_consumable_manufacturer_list():
                return False

            start_page = current_page_position if list_needs_reset else 0
            base_page = 0 if list_needs_reset else current_page_position

            car_result = self.car_cards.find_consumable_action(
                ConsumableCarCardSearchOptions(
                    label="消耗品车辆",
                    max_pages=self.NOT_FOUND_PAGE_LIMIT,
                    start_page=start_page,
                )
            )

            if not car_result:
                self.log(
                    f"从当前页码 {base_page + start_page} 开始连续翻找 {self.NOT_FOUND_PAGE_LIMIT} 页仍未找到可处理消耗品车辆，视为车辆已全部处理完毕。",
                    level="debug",
                )
                self.state.memory_car_page = 0  # 没找到说明车刷完了，清零记忆
                for _ in range(2):
                    self.input_actions.hw_press("esc")
                    sleep(0.8)
                return finish("未找到可处理消耗品车辆")

            current_page_position = base_page + car_result.page_index
            self.state.memory_car_page = current_page_position
            self.input_actions.game_click(car_result.position)
            self.log(
                f"锁定目标车辆，动作: {car_result.action}，已记录当前页码: {current_page_position}",
                level="debug",
            )
            sleep(0.5)

            if car_result.action == "remove":
                if not self._remove_selected_consumable_car():
                    return False
                list_needs_reset = False
                continue

            mastery_result = self._drive_selected_car_and_apply_mastery(effective_target)
            if mastery_result == "技能点不足":
                return finish("技能点不足")
            if mastery_result is not None:
                return False
            list_needs_reset = True

        self.input_actions.hw_press("esc")
        sleep(1.2)
        self.input_actions.hw_press("esc")
        sleep(1.2)
        return finish()
