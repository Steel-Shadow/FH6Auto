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
    color: 'blue',
  },
  { key: 'buy', title: '2. 批量买车', countKey: 'buy_count', color: 'green' },
  {
    key: 'mastery',
    title: '3. 熟练度加点',
    countKey: 'mastery_count',
    useAllKey: 'mastery_use_all',
    useAllLabel: '用完技术点',
    color: 'violet',
  },
  {
    key: 'auto_wheelspin',
    title: '4. 自动抽奖',
    countKey: 'wheelspin_count',
    useAllKey: 'wheelspin_use_all',
    useAllLabel: '用完所有抽奖次数',
    color: 'cyan',
  },
  {
    key: 'sell',
    title: '5. 移除车辆',
    countKey: 'sc_count',
    useAllKey: 'remove_car_use_all',
    useAllLabel: '移除全部',
    color: 'amber',
  },
]

const nextSteps = [
  { value: 1, label: '循环跑图' },
  { value: 2, label: '批量买车' },
  { value: 3, label: '熟练度加点' },
  { value: 4, label: '自动抽奖' },
  { value: 5, label: '移除车辆' },
]

const skillButtons = [
  { value: 'up', label: '↑' },
  { value: 'down', label: '↓' },
  { value: 'left', label: '←' },
  { value: 'right', label: '→' },
]

const wheelspinOptions = [
  {
    label: '超级抽奖',
    countKey: 'wheelspin_count',
  },
  {
    label: '普通抽奖',
    countKey: 'normal_wheelspin_count',
  },
]

const statusLabel = computed(() => {
  if (state.runtime.is_paused) return '已暂停'
  if (state.runtime.is_running) return '运行中'
  return '空闲'
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

async function scrollLogToLatest() {
  await nextTick()
  if (!lockLatestLog.value || !logBox.value) return
  logBox.value.scrollTop = logBox.value.scrollHeight
}

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

function copyConfig(config) {
  Object.keys(draft).forEach((key) => delete draft[key])
  Object.assign(draft, JSON.parse(JSON.stringify(config || {})))
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
  <main class="app-shell">
    <header class="topbar">
      <div>
        <h1>FH6Auto</h1>
        <p>v{{ state.version || '-' }}</p>
      </div>
      <div class="status-cluster">
        <span class="status-pill" :class="state.runtime.status">{{ statusLabel }}</span>
        <button type="button" class="ghost" :disabled="busy" @click="togglePause">
          <span>{{ state.runtime.is_paused ? '继续' : '暂停' }}</span>
          <kbd>F1</kbd>
        </button>
        <button type="button" class="danger" :disabled="busy" @click="stopAll">
          <span>停止</span>
          <kbd>F2</kbd>
        </button>
      </div>
    </header>

    <section class="runtime-band">
      <div>
        <span>当前任务</span>
        <strong>{{ state.runtime.current_task }}</strong>
      </div>
      <div>
        <span>任务进度</span>
        <strong>{{ progressText }}</strong>
      </div>
      <div>
        <span>大循环</span>
        <strong>{{ state.runtime.loop.current || 0 }} / {{ state.runtime.loop.total || 0 }}</strong>
      </div>
      <div>
        <span>总耗时</span>
        <strong>{{ elapsedText }}</strong>
      </div>
    </section>

    <p v-if="errorMessage" class="error-line">{{ errorMessage }}</p>

    <section class="module-grid">
      <article v-for="(module, index) in modules" :key="module.key" class="module-card" :class="module.color">
        <div class="module-title">
          <h2>{{ module.title }}</h2>
          <button type="button" :disabled="busy || state.runtime.is_running" @click="startModule(module.key)">
            开始
          </button>
        </div>
        <div v-if="module.key === 'auto_wheelspin'" class="wheelspin-config">
          <label
            v-for="option in wheelspinOptions"
            :key="option.countKey"
            class="count-control"
            :class="{ disabled: draft[module.useAllKey] }"
          >
            <span>{{ option.label }}次数</span>
            <input
              v-model.number="draft[option.countKey]"
              type="number"
              min="0"
              :disabled="draft[module.useAllKey]"
              @input="dirty = true"
            />
          </label>
          <label class="check-line use-all-toggle" :class="{ active: draft[module.useAllKey] }">
            <input v-model="draft[module.useAllKey]" type="checkbox" @change="dirty = true" />
            <span>{{ module.useAllLabel }}</span>
          </label>
          <label>
            <span>低价车出售阈值(CR)</span>
            <input v-model.number="draft.wheelspin_sell_threshold" type="number" min="0" step="1000" @input="dirty = true" />
          </label>
        </div>
        <label v-else class="count-control" :class="{ disabled: module.useAllKey && draft[module.useAllKey] }">
          <span>执行次数</span>
          <input
            v-model.number="draft[module.countKey]"
            type="number"
            min="0"
            :disabled="module.useAllKey && draft[module.useAllKey]"
            @input="dirty = true"
          />
        </label>
        <label
          v-if="module.useAllKey"
          class="check-line use-all-toggle"
          :class="{ active: draft[module.useAllKey] }"
        >
          <input v-model="draft[module.useAllKey]" type="checkbox" @change="dirty = true" />
          <span>{{ module.useAllLabel }}</span>
        </label>
        <label v-if="module.key === 'race'">
          <span>蓝图代码</span>
          <input v-model="draft.share_code" inputmode="numeric" @input="dirty = true" />
        </label>
        <div class="next-row">
          <label>
            <span>下一步骤</span>
            <select v-model.number="draft[`next_${index + 1}`]" @change="dirty = true">
              <option v-for="step in nextSteps" :key="step.value" :value="step.value">{{ step.label }}</option>
            </select>
          </label>
          <label class="check-line">
            <input v-model="draft[`chk_${index + 1}`]" type="checkbox" @change="dirty = true" />
            <span>继续</span>
          </label>
        </div>
      </article>
    </section>

    <section class="settings-layout">
      <div class="settings-panel">
        <div class="section-title">
          <h2>运行设置</h2>
          <button type="button" :disabled="busy || !dirty" @click="saveConfig">保存配置</button>
        </div>
        <div class="form-grid">
          <label>
            <span>大循环次数</span>
            <input v-model.number="draft.global_loops" type="number" min="1" @input="dirty = true" />
          </label>
          <label>
            <span>制造商扫描步数</span>
            <input v-model.number="draft.manufacturer_scan_steps" type="number" min="5" max="50" @input="dirty = true" />
          </label>
          <label>
            <span>日志级别</span>
            <select v-model="draft.log_level" @change="dirty = true">
              <option value="info">info</option>
              <option value="debug">debug</option>
              <option value="warning">warning</option>
              <option value="error">error</option>
            </select>
          </label>
          <label class="wide">
            <span>启动命令</span>
            <input v-model="draft.restart_cmd" @input="dirty = true" />
          </label>
          <label class="check-line wide">
            <input v-model="draft.auto_restart" type="checkbox" @change="dirty = true" />
            <span>游戏闪退后自动重启</span>
          </label>
        </div>
      </div>

      <div class="settings-panel">
        <div class="section-title">
          <h2>技能路径</h2>
          <button type="button" class="ghost" :disabled="busy" @click="clearSkill">清除</button>
        </div>
        <div class="skill-grid" aria-label="技能树">
          <span v-for="(active, index) in skillCells" :key="index" :class="{ active }"></span>
        </div>
        <div class="skill-buttons">
          <button
            v-for="button in skillButtons"
            :key="button.value"
            type="button"
            :disabled="busy"
            @click="addSkill(button.value)"
          >
            {{ button.label }}
          </button>
        </div>
        <p class="path-text">{{ (draft.skill_dirs || []).join(' / ') || '未设置' }}</p>
      </div>

      <div class="settings-panel">
        <div class="section-title">
          <h2>次数计算器</h2>
          <button type="button" :disabled="busy || !draft.calc_a" @click="calculateAndApply">计算并应用</button>
        </div>
        <div class="form-grid single">
          <label>
            <span>CR</span>
            <input v-model="draft.calc_a" inputmode="numeric" @input="dirty = true" />
          </label>
          <label>
            <span>单车成本</span>
            <input v-model="draft.calc_b" inputmode="numeric" @input="dirty = true" />
          </label>
          <label>
            <span>单车技能点</span>
            <input v-model="draft.calc_c" inputmode="numeric" @input="dirty = true" />
          </label>
          <label>
            <span>单次跑图技能点</span>
            <input v-model="draft.calc_d" inputmode="numeric" @input="dirty = true" />
          </label>
        </div>
      </div>
    </section>

    <section class="log-panel">
      <div class="section-title">
        <h2>运行日志</h2>
        <div class="log-actions">
          <label class="check-line compact">
            <input v-model="lockLatestLog" type="checkbox" @change="scrollLogToLatest" />
            <span>锁定最新</span>
          </label>
          <span>{{ state.runtime.logs.length }} 条</span>
        </div>
      </div>
      <div ref="logBox" class="log-box">
        <p v-for="item in state.runtime.logs" :key="item.id">
          <time>{{ item.time }}</time>
          <span>{{ item.message }}</span>
        </p>
      </div>
    </section>
  </main>
</template>

