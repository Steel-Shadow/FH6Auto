<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'

const state = reactive({
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

const draft = reactive({})
const dirty = ref(false)
const busy = ref(false)
const errorMessage = ref('')
const lockLatestLog = ref(true)
const logBox = ref(null)

const modules = [
  {
    key: 'race',
    title: '1. 循环跑图',
    countKey: 'race_count',
    useAllKey: 'race_until_skill_cap',
    useAllLabel: '达到技术点上限',
    color: '#4da3ff',
  },
  { key: 'buy', title: '2. 批量买车', countKey: 'buy_count', color: '#46c784' },
  {
    key: 'mastery',
    title: '3. 熟练度加点',
    countKey: 'mastery_count',
    useAllKey: 'mastery_use_all',
    useAllLabel: '用完技术点',
    color: '#b487ff',
  },
  {
    key: 'auto_wheelspin',
    title: '4. 自动抽奖',
    countKey: 'wheelspin_count',
    useAllKey: 'wheelspin_use_all',
    useAllLabel: '用完所有抽奖次数',
    color: '#4fc3d7',
  },
  {
    key: 'sell',
    title: '5. 移除车辆',
    countKey: 'sc_count',
    useAllKey: 'remove_car_use_all',
    useAllLabel: '移除全部',
    color: '#f2b84b',
  },
]

const nextSteps = [
  { value: 1, label: '循环跑图' },
  { value: 2, label: '批量买车' },
  { value: 3, label: '熟练度加点' },
  { value: 4, label: '自动抽奖' },
  { value: 5, label: '移除车辆' },
]

const nextStepOptions = [{ value: 0, label: '停止' }, ...nextSteps]
const logLevelItems = ['info', 'debug', 'warning', 'error']

const skillButtons = [
  { value: 'up', label: '↑' },
  { value: 'down', label: '↓' },
  { value: 'left', label: '←' },
  { value: 'right', label: '→' },
]

const wheelspinOptions = [
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

const skillCells = computed(() => {
  const cells = Array.from({ length: 16 }, () => false)
  let row = 3
  let col = 0
  cells[row * 4 + col] = true
  for (const direction of draft.skill_dirs || []) {
    if (direction === 'up') row -= 1
    if (direction === 'down') row += 1
    if (direction === 'left') col -= 1
    if (direction === 'right') col += 1
    if (row < 0 || row > 3 || col < 0 || col > 3) break
    cells[row * 4 + col] = true
  }
  return cells
})

function markDirty() {
  dirty.value = true
}

function setDraftValue(key, value) {
  draft[key] = value
  markDirty()
}

function setDraftNumber(key, value) {
  const number = Number(value)
  draft[key] = Number.isFinite(number) ? number : 0
  markDirty()
}

function nextStepSelectValue(index) {
  const step = index + 1
  return draft[`chk_${step}`] ? Number(draft[`next_${step}`] || step) : 0
}

function updateNextStep(index, value) {
  const step = index + 1
  const nextValue = Number(value)
  draft[`chk_${step}`] = nextValue !== 0
  if (nextValue !== 0) draft[`next_${step}`] = nextValue
  markDirty()
}

function copyConfig(config) {
  Object.keys(draft).forEach((key) => delete draft[key])
  Object.assign(draft, JSON.parse(JSON.stringify(config || {})))
}

async function scrollLogToLatest() {
  await nextTick()
  if (!lockLatestLog.value || !logBox.value) return
  logBox.value.scrollTop = logBox.value.scrollHeight
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const data = await response.json()
      detail = data.detail || detail
    } catch (_err) {
      // ignore non-json errors
    }
    throw new Error(detail)
  }
  return response.json()
}

async function refresh() {
  try {
    const data = await request('/api/state')
    Object.assign(state, data)
    if (!dirty.value) copyConfig(data.config)
    errorMessage.value = ''
  } catch (err) {
    errorMessage.value = err.message || '无法连接后端'
  }
}

async function saveConfig() {
  busy.value = true
  try {
    const configToSave = JSON.parse(JSON.stringify(draft))
    if ('wheelspin_use_all' in configToSave) {
      configToSave.super_wheelspin_use_all = Boolean(configToSave.wheelspin_use_all)
      configToSave.normal_wheelspin_use_all = Boolean(configToSave.wheelspin_use_all)
    }
    const data = await request('/api/config', {
      method: 'PUT',
      body: JSON.stringify({ config: configToSave }),
    })
    copyConfig(data.config)
    dirty.value = false
    await refresh()
  } catch (err) {
    errorMessage.value = err.message
  } finally {
    busy.value = false
  }
}

async function startModule(step) {
  await saveConfig()
  busy.value = true
  try {
    await request('/api/pipeline/start', {
      method: 'POST',
      body: JSON.stringify({ step }),
    })
    await refresh()
  } catch (err) {
    errorMessage.value = err.message
  } finally {
    busy.value = false
  }
}

async function stopAll() {
  busy.value = true
  try {
    await request('/api/pipeline/stop', { method: 'POST', body: '{}' })
    await refresh()
  } catch (err) {
    errorMessage.value = err.message
  } finally {
    busy.value = false
  }
}

async function togglePause() {
  busy.value = true
  try {
    await request('/api/pipeline/pause', { method: 'POST', body: '{}' })
    await refresh()
  } catch (err) {
    errorMessage.value = err.message
  } finally {
    busy.value = false
  }
}

async function setSkillDirs(directions) {
  draft.skill_dirs = directions
  dirty.value = true
  const data = await request('/api/skill-dirs', {
    method: 'POST',
    body: JSON.stringify({ directions }),
  })
  draft.skill_dirs = data.skill_dirs
  dirty.value = false
  await refresh()
}

async function addSkill(direction) {
  await setSkillDirs([...(draft.skill_dirs || []), direction])
}

async function clearSkill() {
  await setSkillDirs([])
}

async function calculateAndApply() {
  busy.value = true
  try {
    const data = await request('/api/tools/calculate', {
      method: 'POST',
      body: JSON.stringify({
        target_cr: Number(draft.calc_a || 0),
        cost_per_car: Number(draft.calc_b || 81700),
        sp_per_car: Number(draft.calc_c || 30),
        sp_per_race: Number(draft.calc_d || 50),
        apply: true,
      }),
    })
    copyConfig(data.config)
    dirty.value = false
    await refresh()
  } catch (err) {
    errorMessage.value = err.message
  } finally {
    busy.value = false
  }
}

onMounted(() => {
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
    <v-main>
      <v-container fluid class="app-shell">
        <v-row align="center" justify="space-between" class="mb-3" dense>
          <v-col cols="12" md="5">
            <div class="app-title">FH6Auto</div>
            <div class="text-medium-emphasis">v{{ state.version || '-' }}</div>
          </v-col>
          <v-col cols="12" md="7" class="d-flex justify-md-end align-center flex-wrap ga-2">
            <v-chip :color="statusColor" variant="tonal" size="large">{{ statusLabel }}</v-chip>
            <v-btn variant="tonal" :disabled="busy" @click="togglePause">
              {{ state.runtime.is_paused ? '继续' : '暂停' }}
              <kbd class="shortcut">F1</kbd>
            </v-btn>
            <v-btn color="error" variant="tonal" :disabled="busy" @click="stopAll">
              停止
              <kbd class="shortcut">F2</kbd>
            </v-btn>
          </v-col>
        </v-row>

        <v-row dense class="mb-3">
          <v-col cols="12" sm="6" lg="3">
            <v-card>
              <v-card-text>
                <div class="text-caption text-medium-emphasis">当前任务</div>
                <div class="runtime-value">{{ state.runtime.current_task }}</div>
              </v-card-text>
            </v-card>
          </v-col>
          <v-col cols="12" sm="6" lg="3">
            <v-card>
              <v-card-text>
                <div class="text-caption text-medium-emphasis">任务进度</div>
                <div class="runtime-value">{{ progressText }}</div>
              </v-card-text>
            </v-card>
          </v-col>
          <v-col cols="12" sm="6" lg="3">
            <v-card>
              <v-card-text>
                <div class="text-caption text-medium-emphasis">大循环</div>
                <div class="runtime-value">{{ state.runtime.loop.current || 0 }} / {{ state.runtime.loop.total || 0 }}</div>
              </v-card-text>
            </v-card>
          </v-col>
          <v-col cols="12" sm="6" lg="3">
            <v-card>
              <v-card-text>
                <div class="text-caption text-medium-emphasis">总耗时</div>
                <div class="runtime-value">{{ elapsedText }}</div>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>

        <v-alert v-if="errorMessage" type="error" variant="tonal" class="mb-3">{{ errorMessage }}</v-alert>

        <v-row dense class="mb-3">
          <v-col v-for="(module, index) in modules" :key="module.key" cols="12" md="6" xl="4">
            <v-card class="module-card" :style="{ borderTopColor: module.color }">
              <v-card-title class="d-flex align-center justify-space-between ga-2">
                <span>{{ module.title }}</span>
                <v-btn color="primary" variant="tonal" :disabled="busy || state.runtime.is_running" @click="startModule(module.key)">
                  开始
                </v-btn>
              </v-card-title>

              <v-card-text>
                <template v-if="module.key === 'auto_wheelspin'">
                  <v-row dense align="center">
                    <v-col v-for="option in wheelspinOptions" :key="option.countKey" cols="12" sm="4">
                      <v-text-field
                        :model-value="draft[option.countKey]"
                        :label="`${option.label}次数`"
                        type="number"
                        min="0"
                        density="compact"
                        hide-details
                        :disabled="draft[module.useAllKey]"
                        @update:model-value="setDraftNumber(option.countKey, $event)"
                      />
                    </v-col>
                    <v-col cols="12" sm="4">
                      <v-checkbox
                        v-model="draft[module.useAllKey]"
                        :label="module.useAllLabel"
                        color="success"
                        density="compact"
                        hide-details
                        @update:model-value="markDirty"
                      />
                    </v-col>
                  </v-row>
                  <v-text-field
                    class="mt-3"
                    :model-value="draft.wheelspin_sell_threshold"
                    label="低价车出售阈值(CR)"
                    type="number"
                    min="0"
                    step="1000"
                    density="compact"
                    hide-details
                    @update:model-value="setDraftNumber('wheelspin_sell_threshold', $event)"
                  />
                </template>

                <template v-else>
                  <v-row dense align="center">
                    <v-col :cols="module.useAllKey ? 7 : 12">
                      <v-text-field
                        :model-value="draft[module.countKey]"
                        label="执行次数"
                        type="number"
                        min="0"
                        density="compact"
                        hide-details
                        :disabled="module.useAllKey && draft[module.useAllKey]"
                        @update:model-value="setDraftNumber(module.countKey, $event)"
                      />
                    </v-col>
                    <v-col v-if="module.useAllKey" cols="5">
                      <v-checkbox
                        v-model="draft[module.useAllKey]"
                        :label="module.useAllLabel"
                        color="success"
                        density="compact"
                        hide-details
                        @update:model-value="markDirty"
                      />
                    </v-col>
                  </v-row>
                </template>

                <v-text-field
                  v-if="module.key === 'race'"
                  class="mt-3"
                  :model-value="draft.share_code"
                  label="蓝图代码"
                  inputmode="numeric"
                  density="compact"
                  hide-details
                  @update:model-value="setDraftValue('share_code', $event)"
                />

                <v-select
                  class="mt-3"
                  :model-value="nextStepSelectValue(index)"
                  label="下一步骤"
                  :items="nextStepOptions"
                  item-title="label"
                  item-value="value"
                  density="compact"
                  hide-details
                  @update:model-value="updateNextStep(index, $event)"
                />
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>

        <v-row dense class="mb-3">
          <v-col cols="12" lg="6">
            <v-card>
              <v-card-title class="d-flex align-center justify-space-between ga-2">
                <span>运行设置</span>
                <v-btn color="primary" variant="tonal" :disabled="busy || !dirty" @click="saveConfig">保存配置</v-btn>
              </v-card-title>
              <v-card-text>
                <v-row dense>
                  <v-col cols="12" sm="4">
                    <v-text-field
                      :model-value="draft.global_loops"
                      label="大循环次数"
                      type="number"
                      min="1"
                      density="compact"
                      hide-details
                      @update:model-value="setDraftNumber('global_loops', $event)"
                    />
                  </v-col>
                  <v-col cols="12" sm="4">
                    <v-text-field
                      :model-value="draft.manufacturer_scan_steps"
                      label="制造商扫描步数"
                      type="number"
                      min="5"
                      max="50"
                      density="compact"
                      hide-details
                      @update:model-value="setDraftNumber('manufacturer_scan_steps', $event)"
                    />
                  </v-col>
                  <v-col cols="12" sm="4">
                    <v-select
                      v-model="draft.log_level"
                      label="日志级别"
                      :items="logLevelItems"
                      density="compact"
                      hide-details
                      @update:model-value="markDirty"
                    />
                  </v-col>
                  <v-col cols="12">
                    <v-text-field
                      :model-value="draft.restart_cmd"
                      label="启动命令"
                      density="compact"
                      hide-details
                      @update:model-value="setDraftValue('restart_cmd', $event)"
                    />
                  </v-col>
                  <v-col cols="12">
                    <v-checkbox
                      v-model="draft.auto_restart"
                      label="游戏闪退后自动重启"
                      color="success"
                      density="compact"
                      hide-details
                      @update:model-value="markDirty"
                    />
                  </v-col>
                </v-row>
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" sm="6" lg="3">
            <v-card>
              <v-card-title class="d-flex align-center justify-space-between ga-2">
                <span>技能路径</span>
                <v-btn variant="tonal" :disabled="busy" @click="clearSkill">清除</v-btn>
              </v-card-title>
              <v-card-text class="text-center">
                <div class="skill-grid" aria-label="技能树">
                  <span v-for="(active, index) in skillCells" :key="index" :class="{ active }"></span>
                </div>
                <div class="d-flex justify-center ga-2 flex-wrap">
                  <v-btn v-for="button in skillButtons" :key="button.value" variant="tonal" :disabled="busy" @click="addSkill(button.value)">
                    {{ button.label }}
                  </v-btn>
                </div>
                <div class="text-body-2 text-medium-emphasis mt-3 path-text">
                  {{ (draft.skill_dirs || []).join(' / ') || '未设置' }}
                </div>
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" sm="6" lg="3">
            <v-card>
              <v-card-title class="d-flex align-center justify-space-between ga-2">
                <span>次数计算器</span>
                <v-btn color="primary" variant="tonal" :disabled="busy || !draft.calc_a" @click="calculateAndApply">
                  计算并应用
                </v-btn>
              </v-card-title>
              <v-card-text>
                <v-row dense>
                  <v-col cols="12">
                    <v-text-field
                      :model-value="draft.calc_a"
                      label="CR"
                      inputmode="numeric"
                      density="compact"
                      hide-details
                      @update:model-value="setDraftValue('calc_a', $event)"
                    />
                  </v-col>
                  <v-col cols="12">
                    <v-text-field
                      :model-value="draft.calc_b"
                      label="单车成本"
                      inputmode="numeric"
                      density="compact"
                      hide-details
                      @update:model-value="setDraftValue('calc_b', $event)"
                    />
                  </v-col>
                  <v-col cols="12">
                    <v-text-field
                      :model-value="draft.calc_c"
                      label="单车技能点"
                      inputmode="numeric"
                      density="compact"
                      hide-details
                      @update:model-value="setDraftValue('calc_c', $event)"
                    />
                  </v-col>
                  <v-col cols="12">
                    <v-text-field
                      :model-value="draft.calc_d"
                      label="单次跑图技能点"
                      inputmode="numeric"
                      density="compact"
                      hide-details
                      @update:model-value="setDraftValue('calc_d', $event)"
                    />
                  </v-col>
                </v-row>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>

        <v-card>
          <v-card-title class="d-flex align-center justify-space-between ga-2">
            <span>运行日志</span>
            <div class="d-flex align-center ga-3">
              <v-checkbox
                v-model="lockLatestLog"
                label="锁定最新"
                color="success"
                density="compact"
                hide-details
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
