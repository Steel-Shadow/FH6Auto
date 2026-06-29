<script lang="ts" setup>
  import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
  import { useTheme } from 'vuetify'

  type DraftValue = string | number | boolean | number[] | undefined

  interface DraftConfig {
    [key: string]: DraftValue
    auto_restart?: boolean
    global_loop_infinite?: boolean
    global_loops?: number
    log_level?: string
    normal_wheelspin_count?: number
    normal_wheelspin_use_all?: boolean
    restart_cmd?: string
    share_code?: string
    skill_cells?: number[]
    super_wheelspin_use_all?: boolean
    wheelspin_sell_threshold?: number
    wheelspin_use_all?: boolean
  }

  interface RuntimeLog {
    id: number | string
    time: string
    message: string
  }

  interface RuntimeState {
    status: string
    is_running: boolean
    is_paused: boolean
    current_task: string
    progress: { current: number, total: number }
    loop: { current: number, total: number }
    elapsed_seconds: number
    logs: RuntimeLog[]
    counters: Record<string, number>
  }

  interface AppState {
    version: string
    runtime: RuntimeState
    config: DraftConfig
  }

  interface ModuleOption {
    key: string
    title: string
    countKey: string
    countLabel?: string
    useAllKey: string
    useAllLabel: string
  }

  interface JsonRequestOptions extends Omit<RequestInit, 'headers'> {
    headers?: Record<string, string>
  }

  interface ConfigResponse {
    config: DraftConfig
  }

  interface SkillCellsResponse {
    skill_cells: number[]
  }

  interface SkillGridPosition {
    col: number
    index: number
    row: number
  }

  interface SkillGridCell {
    col: number
    index: number
    isAvailable: boolean
    isCurrent: boolean
    isStart: boolean
    isVisited: boolean
    label: string
    pathIndex: number
    row: number
  }

  const SKILL_GRID_SIZE = 4
  const SKILL_START_INDEX = 12

  const state = reactive<AppState>({
    version: '',
    runtime: {
      status: 'idle',
      is_running: false,
      is_paused: false,
      current_task: '等待中',
      progress: { current: 0, total: 0 },
      loop: { current: 0, total: 0 },
      elapsed_seconds: 0,
      logs: [],
      counters: {},
    },
    config: {},
  })

  const draft = reactive<DraftConfig>({})
  const dirty = ref(false)
  const busy = ref(false)
  const errorMessage = ref('')
  const lockLatestLog = ref(true)
  const logBox = ref<HTMLElement | null>(null)
  const theme = useTheme()

  const modules: ModuleOption[] = [
    {
      key: 'race',
      title: '1. 循环跑图',
      countKey: 'race_count',
      useAllKey: 'race_until_skill_cap',
      useAllLabel: '达到技术点上限',
    },
    { key: 'buy', title: '2. 批量买车', countKey: 'buy_count', useAllKey: '', useAllLabel: '' },
    {
      key: 'mastery',
      title: '3. 加点&删车',
      countKey: 'mastery_count',
      countLabel: '加点车辆数',
      useAllKey: 'mastery_use_all',
      useAllLabel: '用完技术点',
    },
    {
      key: 'auto_wheelspin',
      title: '4. 自动抽奖',
      countKey: 'wheelspin_count',
      useAllKey: 'wheelspin_use_all',
      useAllLabel: '用完所有抽奖次数',
    },
  ]

  const nextSteps = [
    { value: 1, label: '循环跑图' },
    { value: 2, label: '批量买车' },
    { value: 3, label: '加点&删车' },
    { value: 4, label: '自动抽奖' },
  ]

  const nextStepOptions = [{ value: 0, label: '停止' }, ...nextSteps]
  const logLevelItems = ['info', 'debug', 'warning', 'error']

  const wheelspinOptions: { label: string, countKey: string }[] = [
    { label: '超级抽奖', countKey: 'wheelspin_count' },
    { label: '普通抽奖', countKey: 'normal_wheelspin_count' },
  ]

  const statusLabel = computed(() => {
    if (state.runtime.is_paused) return '已暂停'
    if (state.runtime.is_running) return '运行中'
    return '空闲'
  })

  const statusColor = computed(() => {
    if (state.runtime.is_paused) return 'warning'
    if (state.runtime.is_running) return 'success'
    return 'secondary'
  })

  const elapsedText = computed(() => {
    const sec = Number(state.runtime.elapsed_seconds || 0)
    const h = String(Math.floor(sec / 3600)).padStart(2, '0')
    const m = String(Math.floor((sec % 3600) / 60)).padStart(2, '0')
    const s = String(sec % 60).padStart(2, '0')
    return `${h}:${m}:${s}`
  })

  const progressText = computed(() => {
    const progress = state.runtime.progress || { current: 0, total: 0 }
    return `${progress.current || 0} / ${progress.total || 0}`
  })

  const progressPercent = computed(() => {
    const progress = state.runtime.progress || { current: 0, total: 0 }
    const total = Number(progress.total || 0)
    if (total <= 0) return 0
    const current = Number(progress.current || 0)
    return Math.min(100, Math.max(0, (current / total) * 100))
  })

  const progressPercentText = computed(() => `${Math.round(progressPercent.value)}%`)

  const loopTotalText = computed(() => {
    if (state.runtime.is_running && state.runtime.loop.total <= 0) return '∞'
    if (draftBoolean('global_loop_infinite')) return '∞'
    return String(state.runtime.loop.total || 0)
  })

  const isDarkTheme = computed(() => theme.global.current.value.dark)
  const themeButtonIcon = computed(() => (isDarkTheme.value ? 'mdi-weather-sunny' : 'mdi-weather-night'))
  const themeButtonLabel = computed(() => (isDarkTheme.value ? '切换到亮色主题' : '切换到暗色主题'))

  function skillCellFromIndex (index: number): SkillGridPosition | null {
    if (!Number.isInteger(index) || index < 0 || index >= SKILL_GRID_SIZE * SKILL_GRID_SIZE) return null
    return {
      col: index % SKILL_GRID_SIZE,
      index,
      row: Math.floor(index / SKILL_GRID_SIZE),
    }
  }

  function isAdjacentSkillCell (left: SkillGridPosition, right: SkillGridPosition) {
    return Math.abs(left.row - right.row) + Math.abs(left.col - right.col) === 1
  }

  function normalizeSkillCellIndexes (value: unknown): number[] {
    if (!Array.isArray(value)) return []

    const start = skillCellFromIndex(SKILL_START_INDEX)
    if (!start) return []

    const cells = Array.from(new Set(value.map(Number)))
      .filter(index => index !== SKILL_START_INDEX && skillCellFromIndex(index) !== null)
      .toSorted((left, right) => left - right)
    const connected = new Set([SKILL_START_INDEX])
    const remaining = new Set(cells)
    let changed = true

    while (changed) {
      changed = false
      for (const index of Array.from(remaining)) {
        const cell = skillCellFromIndex(index)
        if (!cell) continue
        const canConnect = Array.from(connected).some(connectedIndex => {
          const connectedCell = skillCellFromIndex(connectedIndex)
          return connectedCell ? isAdjacentSkillCell(connectedCell, cell) : false
        })
        if (!canConnect) continue

        connected.add(index)
        remaining.delete(index)
        changed = true
      }
    }

    return cells.filter(index => connected.has(index))
  }

  const selectedSkillCells = computed(() => {
    return normalizeSkillCellIndexes(draft.skill_cells)
  })

  const skillPath = computed<SkillGridPosition[]>(() => {
    return [SKILL_START_INDEX, ...selectedSkillCells.value]
      .map(index => skillCellFromIndex(index))
      .filter((cell): cell is SkillGridPosition => cell !== null)
  })

  const skillGridCells = computed<SkillGridCell[]>(() => {
    const pathIndexByCell = new Map(skillPath.value.map((cell, index) => [cell.index, index]))

    return Array.from({ length: SKILL_GRID_SIZE * SKILL_GRID_SIZE }, (_, index) => {
      const cell = skillCellFromIndex(index)
      if (!cell) throw new Error(`Invalid skill cell index: ${index}`)
      const pathIndex = pathIndexByCell.get(index) ?? -1
      const isVisited = pathIndex >= 0
      const isStart = index === SKILL_START_INDEX
      const isAdjacentToSelected = skillPath.value.some(selectedCell => isAdjacentSkillCell(selectedCell, cell))
      let label = ''
      if (isStart) {
        label = '起'
      } else if (isVisited) {
        label = '✓'
      }

      return {
        col: cell.col,
        index,
        isAvailable: isAdjacentToSelected && !isVisited,
        isCurrent: false,
        isStart,
        isVisited,
        label,
        pathIndex,
        row: cell.row,
      }
    })
  })

  const skillPathText = computed(() => {
    const extraCells = skillPath.value.length - 1
    return extraCells > 0 ? `已选择 ${extraCells} 个额外加点格，起点会自动加点` : '起点会自动加点，请选择额外技能格'
  })

  function skillCellColor (cell: SkillGridCell) {
    if (cell.isCurrent) return 'primary'
    if (cell.isVisited) return 'primary'
    if (cell.isAvailable) return 'secondary'
    return undefined
  }

  function skillCellVariant (cell: SkillGridCell) {
    if (cell.isCurrent) return 'flat'
    if (cell.isVisited) return 'tonal'
    if (cell.isAvailable) return 'outlined'
    return 'outlined'
  }

  function messageFromError (error: unknown, fallback = '操作失败') {
    return error instanceof Error ? error.message : fallback
  }

  function draftBoolean (key: string) {
    return Boolean(key && draft[key])
  }

  function markDirty () {
    dirty.value = true
  }

  function setDraftValue (key: string, value: DraftValue) {
    draft[key] = value
    markDirty()
  }

  function setDraftNumber (key: string, value: unknown) {
    const number = Number(value)
    draft[key] = Number.isFinite(number) ? number : 0
    markDirty()
  }

  function nextStepSelectValue (index: number) {
    const step = index + 1
    return draft[`chk_${step}`] ? Number(draft[`next_${step}`] || step) : 0
  }

  function updateNextStep (index: number, value: unknown) {
    const step = index + 1
    const nextValue = Number(value)
    draft[`chk_${step}`] = nextValue !== 0
    if (nextValue !== 0) draft[`next_${step}`] = nextValue
    markDirty()
  }

  function cloneConfig (config: DraftConfig | null | undefined): DraftConfig {
    return Object.fromEntries(
      Object.entries(config || {}).map(([key, value]) => [
        key,
        Array.isArray(value) ? [...value] : value,
      ]),
    ) as DraftConfig
  }

  function copyConfig (config: DraftConfig | null | undefined) {
    for (const key of Object.keys(draft)) delete draft[key]
    Object.assign(draft, cloneConfig(config))
  }

  async function scrollLogToLatest () {
    await nextTick()
    if (!lockLatestLog.value || !logBox.value) return
    logBox.value.scrollTop = logBox.value.scrollHeight
  }

  async function request<T = unknown> (path: string, options: JsonRequestOptions = {}): Promise<T> {
    const { headers, ...requestOptions } = options
    const response = await fetch(path, {
      ...requestOptions,
      headers: { 'Content-Type': 'application/json', ...headers },
    })
    if (!response.ok) {
      let detail = response.statusText
      try {
        const data = await response.json() as { detail?: string }
        detail = data.detail || detail
      } catch {
      // ignore non-json errors
      }
      throw new Error(detail)
    }
    return response.json() as Promise<T>
  }

  async function refresh () {
    try {
      const data = await request<AppState>('/api/state')
      Object.assign(state, data)
      if (!dirty.value) copyConfig(data.config)
      errorMessage.value = ''
    } catch (error) {
      errorMessage.value = messageFromError(error, '无法连接后端')
    }
  }

  async function saveConfig () {
    busy.value = true
    try {
      const configToSave = cloneConfig(draft)
      if ('wheelspin_use_all' in configToSave) {
        configToSave.super_wheelspin_use_all = Boolean(configToSave.wheelspin_use_all)
        configToSave.normal_wheelspin_use_all = Boolean(configToSave.wheelspin_use_all)
      }
      const data = await request<ConfigResponse>('/api/config', {
        method: 'PUT',
        body: JSON.stringify({ config: configToSave }),
      })
      copyConfig(data.config)
      dirty.value = false
      await refresh()
    } catch (error) {
      errorMessage.value = messageFromError(error)
    } finally {
      busy.value = false
    }
  }

  async function startModule (step: string) {
    await saveConfig()
    busy.value = true
    try {
      await request('/api/pipeline/start', {
        method: 'POST',
        body: JSON.stringify({ step }),
      })
      await refresh()
    } catch (error) {
      errorMessage.value = messageFromError(error)
    } finally {
      busy.value = false
    }
  }

  async function stopAll () {
    busy.value = true
    try {
      await request('/api/pipeline/stop', { method: 'POST', body: '{}' })
      await refresh()
    } catch (error) {
      errorMessage.value = messageFromError(error)
    } finally {
      busy.value = false
    }
  }

  async function toggleRun () {
    if (state.runtime.is_running) {
      await stopAll()
      return
    }
    await startModule('race')
  }

  async function togglePause () {
    busy.value = true
    try {
      await request('/api/pipeline/pause', { method: 'POST', body: '{}' })
      await refresh()
    } catch (error) {
      errorMessage.value = messageFromError(error)
    } finally {
      busy.value = false
    }
  }

  async function setSkillCells (cells: number[]) {
    draft.skill_cells = cells
    dirty.value = true
    const data = await request<SkillCellsResponse>('/api/skill-cells', {
      method: 'POST',
      body: JSON.stringify({ cells }),
    })
    draft.skill_cells = data.skill_cells
    dirty.value = false
    await refresh()
  }

  async function selectSkillCell (cell: SkillGridCell) {
    if (cell.isStart) return
    if (cell.isVisited) {
      await setSkillCells(selectedSkillCells.value.filter(index => index !== cell.index))
      return
    }
    if (!cell.isAvailable) return
    await setSkillCells([...selectedSkillCells.value, cell.index])
  }

  async function clearSkill () {
    await setSkillCells([])
  }

  function setTheme (name: 'light' | 'dark') {
    theme.global.name.value = name
    window.localStorage.setItem('fh6auto-theme', name)
  }

  function toggleTheme () {
    setTheme(isDarkTheme.value ? 'light' : 'dark')
  }

  onMounted(() => {
    const savedTheme = window.localStorage.getItem('fh6auto-theme')
    if (savedTheme === 'light' || savedTheme === 'dark') {
      theme.global.name.value = savedTheme
    }
    refresh()
    window.setInterval(refresh, 1000)
  })

  watch(
    () => state.runtime.logs.length,
    () => scrollLogToLatest(),
    { flush: 'post' },
  )
</script>

<template>
  <v-app>
    <v-app-bar density="comfortable" elevation="1">
      <v-app-bar-title>FH6Auto v{{ state.version || '-' }}</v-app-bar-title>

      <template #append>
        <div class="d-flex align-center ga-2">
          <div class="app-bar-stats d-none d-md-flex align-center ga-2">
            <div class="app-bar-stat app-bar-task">
              <span class="stat-label">当前任务</span>
              <span class="stat-value text-truncate">{{ state.runtime.current_task || '等待中' }}</span>
            </div>

            <v-chip :color="statusColor" size="small" variant="tonal">{{ statusLabel }}</v-chip>

            <div class="app-bar-stat">
              <span class="stat-label">当前进度</span>
              <span class="stat-value">{{ progressText }} · 循环 {{ state.runtime.loop.current || 0 }}/{{ loopTotalText }}</span>
            </div>

            <v-chip color="primary" size="small" variant="tonal">{{ progressPercentText }}</v-chip>

            <div class="app-bar-stat">
              <span class="stat-label">总耗时</span>
              <span class="stat-value">{{ elapsedText }}</span>
            </div>
          </div>

          <v-btn
            :aria-label="themeButtonLabel"
            :icon="themeButtonIcon"
            :title="themeButtonLabel"
            variant="tonal"
            @click="toggleTheme"
          />

          <v-btn :disabled="busy" variant="tonal" @click="togglePause">
            {{ state.runtime.is_paused ? '继续' : '暂停' }}
            <kbd class="shortcut">F1</kbd>
          </v-btn>

          <v-btn
            :color="state.runtime.is_running ? 'error' : 'success'"
            :disabled="busy"
            variant="tonal"
            @click="toggleRun"
          >
            {{ state.runtime.is_running ? '停止' : '开始' }}
            <kbd class="shortcut">F2</kbd>
          </v-btn>
        </div>
      </template>
    </v-app-bar>

    <v-main>
      <v-container class="app-shell py-4" fluid>
        <v-alert v-if="errorMessage" class="mb-3" type="error" variant="tonal">{{ errorMessage }}</v-alert>

        <v-row class="mb-3">
          <v-col
            v-for="(module, index) in modules"
            :key="module.key"
            cols="12"
            md="3"
            sm="6"
          >
            <v-card class="h-100 d-flex flex-column" variant="outlined">
              <v-card-title class="d-flex align-center justify-space-between ga-2">
                <span>{{ module.title }}</span>

                <v-btn
                  color="primary"
                  :disabled="busy || state.runtime.is_running"
                  variant="tonal"
                  @click="startModule(module.key)"
                >
                  开始
                </v-btn>
              </v-card-title>

              <v-card-text class="flex-grow-1">
                <template v-if="module.key === 'auto_wheelspin'">
                  <v-row align="center">
                    <v-col v-for="option in wheelspinOptions" :key="option.countKey">
                      <v-text-field
                        density="compact"
                        :disabled="draftBoolean(module.useAllKey)"
                        hide-details
                        :label="`${option.label}次数`"
                        min="0"
                        :model-value="draft[option.countKey]"
                        type="number"
                        @update:model-value="setDraftNumber(option.countKey, $event)"
                      />
                    </v-col>
                  </v-row>

                  <v-checkbox
                    v-model="draft[module.useAllKey]"
                    color="success"
                    density="compact"
                    hide-details
                    :label="module.useAllLabel"
                    @update:model-value="markDirty"
                  />

                  <v-text-field
                    class="mt-3"
                    density="compact"
                    hide-details
                    label="低价车出售阈值(CR)"
                    min="0"
                    :model-value="draft.wheelspin_sell_threshold"
                    step="1000"
                    type="number"
                    @update:model-value="setDraftNumber('wheelspin_sell_threshold', $event)"
                  />
                </template>

                <template v-else>
                  <v-row align="center">
                    <v-col>
                      <v-text-field
                        density="compact"
                        :disabled="draftBoolean(module.useAllKey)"
                        hide-details
                        :label="module.countLabel || '执行次数'"
                        min="0"
                        :model-value="draft[module.countKey]"
                        type="number"
                        @update:model-value="setDraftNumber(module.countKey, $event)"
                      />
                    </v-col>

                    <v-col v-if="module.useAllKey">
                      <v-checkbox
                        v-model="draft[module.useAllKey]"
                        color="success"
                        density="compact"
                        hide-details
                        :label="module.useAllLabel"
                        @update:model-value="markDirty"
                      />
                    </v-col>
                  </v-row>
                </template>

                <v-text-field
                  v-if="module.key === 'race'"
                  class="mt-3"
                  density="compact"
                  hide-details
                  inputmode="numeric"
                  label="蓝图代码"
                  :model-value="draft.share_code"
                  @update:model-value="setDraftValue('share_code', $event)"
                />

                <v-select
                  class="mt-3"
                  density="compact"
                  hide-details
                  item-title="label"
                  item-value="value"
                  :items="nextStepOptions"
                  label="下一步骤"
                  :model-value="nextStepSelectValue(index)"
                  @update:model-value="updateNextStep(index, $event)"
                />
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>

        <v-row align="stretch" class="mb-3">
          <v-col>
            <v-card class="h-100 d-flex flex-column" variant="outlined">
              <v-card-title class="d-flex align-center justify-space-between ga-2">
                <span>运行设置</span>
                <v-btn color="primary" :disabled="busy || !dirty" variant="tonal" @click="saveConfig">保存配置</v-btn>
              </v-card-title>

              <v-card-text class="flex-grow-1">
                <v-row>
                  <v-col cols="12" sm="4">
                    <v-text-field
                      density="compact"
                      :disabled="draftBoolean('global_loop_infinite')"
                      hide-details
                      label="大循环次数"
                      min="1"
                      :model-value="draft.global_loops"
                      type="number"
                      @update:model-value="setDraftNumber('global_loops', $event)"
                    />
                  </v-col>

                  <v-col class="d-flex align-center" cols="12" sm="4">
                    <v-checkbox
                      v-model="draft.global_loop_infinite"
                      color="success"
                      density="compact"
                      hide-details
                      label="无限循环"
                      @update:model-value="markDirty"
                    />
                  </v-col>

                  <v-col cols="12" sm="4">
                    <v-select
                      v-model="draft.log_level"
                      density="compact"
                      hide-details
                      :items="logLevelItems"
                      label="日志级别"
                      @update:model-value="markDirty"
                    />
                  </v-col>

                  <v-col cols="12">
                    <v-text-field
                      density="compact"
                      hide-details
                      label="启动命令"
                      :model-value="draft.restart_cmd"
                      @update:model-value="setDraftValue('restart_cmd', $event)"
                    />
                  </v-col>

                  <v-col cols="12">
                    <v-checkbox
                      v-model="draft.auto_restart"
                      color="success"
                      density="compact"
                      hide-details
                      label="游戏闪退后自动重启"
                      @update:model-value="markDirty"
                    />
                  </v-col>
                </v-row>
              </v-card-text>
            </v-card>
          </v-col>

          <v-col>
            <v-card class="h-100 d-flex flex-column" variant="outlined">
              <v-card-title class="d-flex align-center justify-space-between ga-2">
                <span>技能路径</span>
                <v-btn :disabled="busy" variant="tonal" @click="clearSkill">清除</v-btn>
              </v-card-title>

              <v-card-text class="flex-grow-1 text-center">
                <div aria-label="技能树" class="skill-grid mb-3">
                  <v-btn
                    v-for="cell in skillGridCells"
                    :key="cell.index"
                    class="skill-cell"
                    :color="skillCellColor(cell)"
                    :disabled="busy || cell.isStart || (!cell.isAvailable && !cell.isVisited)"
                    :ripple="cell.isAvailable || (cell.isVisited && !cell.isStart)"
                    :variant="skillCellVariant(cell)"
                    @click="selectSkillCell(cell)"
                  >
                    {{ cell.label }}
                  </v-btn>
                </div>

                <div class="text-body-2 text-medium-emphasis path-text">
                  {{ skillPathText }}
                </div>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>

        <v-card variant="outlined">
          <v-card-title class="d-flex align-center justify-space-between ga-2">
            <span>运行日志</span>

            <div class="d-flex align-center ga-3">
              <v-checkbox
                v-model="lockLatestLog"
                color="success"
                density="compact"
                hide-details
                label="锁定最新"
                @update:model-value="scrollLogToLatest"
              />

              <span class="text-body-2 text-medium-emphasis">{{ state.runtime.logs.length }} 条</span>
            </div>
          </v-card-title>

          <v-card-text>
            <div ref="logBox" class="log-box">
              <p v-for="item in state.runtime.logs" :key="item.id" class="log-line">
                <time>{{ item.time }}</time>
                <span>{{ item.message }}</span>
              </p>
            </div>
          </v-card-text>
        </v-card>
      </v-container>
    </v-main>
  </v-app>
</template>

<style scoped>
  .app-bar-stats {
    max-width: min(62vw, 900px);
    min-width: 0;
  }

  .app-bar-stat {
    border-inline-start: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
    display: flex;
    flex-direction: column;
    line-height: 1.15;
    min-width: 92px;
    padding-inline-start: 10px;
  }

  .app-bar-task {
    max-width: 240px;
  }

  .stat-label {
    color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
    font-size: 0.72rem;
    white-space: nowrap;
  }

  .stat-value {
    font-size: 0.9rem;
    font-weight: 600;
    white-space: nowrap;
  }

  .skill-grid {
    display: grid;
    gap: 8px;
    grid-template-columns: repeat(4, 42px);
    justify-content: center;
  }

  .skill-cell {
    height: 42px;
    min-width: 42px;
    padding: 0;
    width: 42px;
  }

  .log-box {
    max-height: 280px;
    overflow-y: auto;
    padding-right: 6px;
  }

  .log-line {
    display: flex;
    gap: 10px;
    margin: 0 0 4px;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .log-line time {
    color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
    flex: 0 0 auto;
  }
</style>
