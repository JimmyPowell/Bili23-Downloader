<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, BatchJob, clearToken, Episode, formatBytes, imageUrl, ParsedResult, setToken, Task } from './api'

const setupRequired = ref(false)
const authed = ref(false)
const username = ref('admin')
const password = ref('')
const message = ref('')
const active = ref('parse')
const parseUrl = ref('')
const parsed = ref<ParsedResult | null>(null)
const selected = ref<Set<string>>(new Set())
const tasks = ref<Task[]>([])
const batchJobs = ref<BatchJob[]>([])
const logs = ref<any[]>([])
const files = ref<any[]>([])
const settings = ref<Record<string, any>>({})
const account = ref<any>({})
const qr = ref<any>(null)
const busy = ref('')
const loadError = ref('')
let timer = 0

const selectedEpisodes = computed(() => {
  if (!parsed.value) return []
  return parsed.value.episodes.filter((ep) => selected.value.has(`${ep.bvid}:${ep.cid}`))
})

async function health() {
  const data = await api<{ setup_required: boolean }>('/api/health')
  setupRequired.value = data.setup_required
  authed.value = !data.setup_required && !!localStorage.getItem('bili23_token')
}

async function bootstrap() {
  const data = await api<{ token: string }>('/api/auth/bootstrap', {
    method: 'POST',
    body: JSON.stringify({ username: username.value, password: password.value })
  })
  setToken(data.token)
  authed.value = true
  setupRequired.value = false
  await loadAll()
}

async function login() {
  const data = await api<{ token: string }>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username: username.value, password: password.value })
  })
  setToken(data.token)
  authed.value = true
  await loadAll()
}

function logout() {
  clearToken()
  authed.value = false
}

async function loadAll() {
  await Promise.allSettled([loadTasks(), loadBatchJobs(), loadSettings(), loadAccount(), loadLogs(), loadFiles()])
  window.clearInterval(timer)
  timer = window.setInterval(() => {
    if (authed.value) void Promise.allSettled([loadTasks(), loadBatchJobs(), loadLogs(), loadFiles()])
  }, 1500)
}

async function loadTasks() {
  await safeLoad(async () => {
    tasks.value = await api<Task[]>('/api/tasks')
  })
}

async function loadBatchJobs() {
  await safeLoad(async () => {
    batchJobs.value = await api<BatchJob[]>('/api/batch-jobs')
  })
}

async function loadSettings() {
  await safeLoad(async () => {
    settings.value = await api<Record<string, any>>('/api/settings')
  })
}

async function saveSettings() {
  await runAction('settings', async () => {
    settings.value = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ values: settings.value }) })
    message.value = '设置已保存'
  })
}

async function loadAccount() {
  await safeLoad(async () => {
    account.value = await api('/api/bilibili/account')
  })
}

async function startQr() {
  qr.value = await api('/api/bilibili/qrcode/start', { method: 'POST', body: '{}' })
}

async function pollQr() {
  const data = await api<any>('/api/bilibili/qrcode/status')
  message.value = data.message || `扫码状态：${data.code}`
  if (data.code === 0) {
    qr.value = null
    await loadAccount()
  }
}

async function parse() {
  await runAction('parse', async () => {
    message.value = '正在解析...'
    parsed.value = await api<ParsedResult>('/api/parse', { method: 'POST', body: JSON.stringify({ url: parseUrl.value }) })
    selected.value = new Set(parsed.value.episodes.map((ep) => `${ep.bvid}:${ep.cid}`))
    message.value = `解析完成：${parsed.value.episodes.length} 个条目`
  })
}

function toggle(ep: Episode) {
  const key = `${ep.bvid}:${ep.cid}`
  const next = new Set(selected.value)
  next.has(key) ? next.delete(key) : next.add(key)
  selected.value = next
}

async function createTasks() {
  await runAction('tasks-create', async () => {
    await api('/api/tasks', {
      method: 'POST',
      body: JSON.stringify({ episodes: selectedEpisodes.value, options: { source: parseUrl.value } })
    })
    message.value = `已创建 ${selectedEpisodes.value.length} 个下载任务`
    active.value = 'tasks'
    await loadTasks()
  })
}

async function createBatchJob() {
  await runAction('batch-create', async () => {
    if (!parseUrl.value.trim()) throw new Error('请先填写个人主页链接')
    const job = await api<BatchJob>('/api/batch-jobs', {
      method: 'POST',
      body: JSON.stringify({
        url: parseUrl.value,
        options: {
          mode: 'slow_page_batch',
          page_size: parsed.value?.pagination?.page_size || 30,
          page_delay_seconds: 3
        }
      })
    })
    message.value = job?.id ? `慢速批量下载已创建：${job.id.slice(0, 8)}` : '慢速批量下载已创建'
    active.value = 'tasks'
    await Promise.allSettled([loadTasks(), loadBatchJobs()])
  })
}

async function batchAction(job: BatchJob, action: 'pause' | 'resume' | 'cancel') {
  await runAction(`batch-${job.id}-${action}`, async () => {
    await api(`/api/batch-jobs/${job.id}/${action}`, { method: 'POST', body: '{}' })
    message.value = `慢速批量任务已${actionText(action)}`
    await Promise.allSettled([loadBatchJobs(), loadTasks()])
  })
}

async function taskAction(task: Task, action: 'pause' | 'resume' | 'cancel') {
  await runAction(`task-${task.id}-${action}`, async () => {
    await api(`/api/tasks/${task.id}/${action}`, { method: 'POST', body: '{}' })
    message.value = `任务已${actionText(action)}`
    await loadTasks()
  })
}

async function taskBulk(action: 'pause' | 'resume' | 'cancel') {
  const candidates = tasks.value.filter((task) => action === 'resume' ? task.status === 'paused' : !['completed', 'failed', 'cancelled'].includes(task.status))
  await runAction(`task-bulk-${action}`, async () => {
    await Promise.all(candidates.map((task) => api(`/api/tasks/${task.id}/${action}`, { method: 'POST', body: '{}' })))
    message.value = `${candidates.length} 个任务已${actionText(action)}`
    await loadTasks()
  })
}

async function loadLogs() {
  await safeLoad(async () => {
    logs.value = await api('/api/logs')
  })
}

async function loadFiles() {
  await safeLoad(async () => {
    files.value = await api('/api/files')
  })
}

async function safeLoad(fn: () => Promise<void>) {
  try {
    await fn()
    loadError.value = ''
  } catch (error: any) {
    loadError.value = error.message || String(error)
  }
}

async function runAction(name: string, fn: () => Promise<void>) {
  busy.value = name
  try {
    await fn()
  } catch (error: any) {
    message.value = error.message || String(error)
  } finally {
    busy.value = ''
  }
}

function actionText(action: 'pause' | 'resume' | 'cancel') {
  return action === 'pause' ? '暂停' : action === 'resume' ? '继续' : '取消'
}

onMounted(async () => {
  try {
    await health()
    if (authed.value) await loadAll()
  } catch (error: any) {
    message.value = error.message
  }
})
</script>

<template>
  <main v-if="!authed" class="auth-shell">
    <section class="auth-panel">
      <h1>Bili23 Web</h1>
      <p>{{ setupRequired ? '初始化管理员账号' : '登录管理台' }}</p>
      <input v-model="username" placeholder="用户名" />
      <input v-model="password" placeholder="密码" type="password" />
      <button @click="setupRequired ? bootstrap() : login()">{{ setupRequired ? '初始化' : '登录' }}</button>
      <small>{{ message }}</small>
    </section>
  </main>

  <main v-else class="app-shell">
    <aside>
      <div class="brand">Bili23 Web</div>
      <button :class="{ active: active === 'parse' }" @click="active = 'parse'">解析下载</button>
      <button :class="{ active: active === 'tasks' }" @click="active = 'tasks'">任务队列</button>
      <button :class="{ active: active === 'account' }" @click="active = 'account'">B站账号</button>
      <button :class="{ active: active === 'settings' }" @click="active = 'settings'">设置</button>
      <button :class="{ active: active === 'files' }" @click="active = 'files'">文件</button>
      <button :class="{ active: active === 'logs' }" @click="active = 'logs'">日志</button>
      <button class="ghost" @click="logout">退出</button>
    </aside>

    <section class="workspace">
      <header>
        <strong>{{ active }}</strong>
        <span>{{ busy ? '处理中...' : (loadError ? `接口异常：${loadError}` : message) }}</span>
      </header>

      <section v-if="active === 'parse'" class="pane">
        <div class="url-row">
          <input v-model="parseUrl" placeholder="粘贴 BV 视频链接或 B 站个人主页链接" @keyup.enter="parse" />
          <button :disabled="busy === 'parse'" @click="parse">{{ busy === 'parse' ? '解析中' : '解析' }}</button>
        </div>
        <div v-if="parsed" class="parsed">
          <img :src="imageUrl(parsed.cover)" alt="" />
          <div>
            <h2>{{ parsed.title }}</h2>
            <p>
              {{ parsed.uploader }}
              <template v-if="parsed.bvid"> · {{ parsed.bvid }}</template>
              <template v-if="parsed.pagination"> · 第 {{ parsed.pagination.current_page }}/{{ parsed.pagination.total_pages }} 页，共 {{ parsed.pagination.total_items }} 个投稿</template>
            </p>
            <button :disabled="busy === 'tasks-create' || !selectedEpisodes.length" @click="createTasks">创建下载任务（{{ selectedEpisodes.length }}）</button>
            <button :disabled="busy === 'batch-create'" @click="createBatchJob">{{ busy === 'batch-create' ? '创建中' : '慢速批量下载' }}</button>
          </div>
        </div>
        <table v-if="parsed">
          <thead><tr><th></th><th>分集</th><th>标题</th><th>CID</th><th>时长</th></tr></thead>
          <tbody>
            <tr v-for="ep in parsed.episodes" :key="`${ep.bvid}:${ep.cid}`">
              <td><input type="checkbox" :checked="selected.has(`${ep.bvid}:${ep.cid}`)" @change="toggle(ep)" /></td>
              <td>{{ ep.part }}</td>
              <td>{{ ep.title }}</td>
              <td>{{ ep.cid }}</td>
              <td>{{ Math.round(ep.duration / 60) }} 分钟</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="active === 'tasks'" class="pane">
        <div class="toolbar">
          <button :disabled="busy === 'task-bulk-pause'" @click="taskBulk('pause')">全部暂停</button>
          <button :disabled="busy === 'task-bulk-resume'" @click="taskBulk('resume')">全部继续</button>
          <button class="ghost" :disabled="busy === 'task-bulk-cancel'" @click="taskBulk('cancel')">全部取消</button>
        </div>
        <div v-if="batchJobs.length" class="batch-panel">
          <h2>慢速批量下载</h2>
          <table>
            <thead><tr><th>来源</th><th>状态</th><th>页数</th><th>已创建</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="job in batchJobs" :key="job.id">
                <td>{{ job.source_url }}<small>{{ job.error }}</small></td>
                <td><span class="badge">{{ job.status }}</span></td>
                <td>第 {{ job.current_page }}/{{ job.total_pages }} 页，已完成 {{ job.completed_pages }} 页</td>
                <td>{{ job.created }} / {{ job.total_items || job.total }}</td>
                <td>
                  <button :disabled="busy.startsWith(`batch-${job.id}`) || job.status === 'paused'" @click="batchAction(job, 'pause')">暂停</button>
                  <button :disabled="busy.startsWith(`batch-${job.id}`) || !['paused', 'failed'].includes(job.status)" @click="batchAction(job, 'resume')">继续</button>
                  <button :disabled="busy.startsWith(`batch-${job.id}`) || ['completed', 'cancelled'].includes(job.status)" @click="batchAction(job, 'cancel')">取消</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-state">暂无慢速批量任务</div>
        <table v-if="tasks.length">
          <thead><tr><th>标题</th><th>状态</th><th>进度</th><th>速度</th><th>输出</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="task in tasks" :key="task.id">
              <td>{{ task.title }}<small>{{ task.error }}</small></td>
              <td><span class="badge">{{ task.status }}</span></td>
              <td><progress :value="task.progress" max="100"></progress> {{ task.progress.toFixed(1) }}%</td>
              <td>{{ formatBytes(task.speed) }}/s</td>
              <td>{{ task.output_file || task.output_dir }}</td>
              <td>
                <button :disabled="busy.startsWith(`task-${task.id}`) || ['paused', 'completed', 'failed', 'cancelled'].includes(task.status)" @click="taskAction(task, 'pause')">暂停</button>
                <button :disabled="busy.startsWith(`task-${task.id}`) || task.status !== 'paused'" @click="taskAction(task, 'resume')">继续</button>
                <button :disabled="busy.startsWith(`task-${task.id}`) || ['completed', 'cancelled'].includes(task.status)" @click="taskAction(task, 'cancel')">取消</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state">暂无下载任务。创建任务后这里会显示进度、速度和输出路径。</div>
      </section>

      <section v-if="active === 'account'" class="pane account">
        <div class="account-card">
          <img v-if="account.face" :src="imageUrl(account.face)" alt="" />
          <div>
            <h2>{{ account.is_login ? account.uname : '未登录 B 站' }}</h2>
            <p>{{ account.is_login ? `UID ${account.mid} · Lv ${account.level}` : account.error }}</p>
            <button @click="startQr">扫码登录</button>
            <button v-if="qr" @click="pollQr">刷新扫码状态</button>
          </div>
        </div>
        <img v-if="qr" class="qr" :src="qr.image" alt="" />
      </section>

      <section v-if="active === 'settings'" class="pane settings">
        <label>下载目录 <input v-model="settings.download_dir" /></label>
        <label>并发任务 <input v-model.number="settings.max_concurrent" type="number" min="1" /></label>
        <label>视频清晰度 <input v-model.number="settings.video_quality" type="number" /></label>
        <label>音频质量 <input v-model.number="settings.audio_quality" type="number" /></label>
        <label>命名模板 <input v-model="settings.filename_template" /></label>
        <label><input v-model="settings.merge_av" type="checkbox" /> 合并音视频</label>
        <label><input v-model="settings.keep_parts" type="checkbox" /> 保留分片</label>
        <label><input v-model="settings.download_cover" type="checkbox" /> 下载封面</label>
        <label><input v-model="settings.download_danmaku" type="checkbox" /> 下载弹幕</label>
        <label><input v-model="settings.download_metadata" type="checkbox" /> 写入元数据 JSON</label>
        <button @click="saveSettings">保存设置</button>
      </section>

      <section v-if="active === 'files'" class="pane">
        <table>
          <thead><tr><th>文件</th><th>大小</th><th>路径</th></tr></thead>
          <tbody><tr v-for="file in files" :key="file.path"><td>{{ file.name }}</td><td>{{ formatBytes(file.size) }}</td><td>{{ file.path }}</td></tr></tbody>
        </table>
      </section>

      <section v-if="active === 'logs'" class="pane">
        <div v-for="log in logs" :key="log.id" class="log-line">
          <span>{{ log.level }}</span><p>{{ log.message }}</p>
        </div>
      </section>
    </section>
  </main>
</template>
