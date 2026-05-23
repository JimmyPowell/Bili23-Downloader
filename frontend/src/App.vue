<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, BatchJob, clearToken, Episode, formatBytes, ParsedResult, setToken, Task } from './api'

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
  await Promise.all([loadTasks(), loadBatchJobs(), loadSettings(), loadAccount(), loadLogs(), loadFiles()])
  window.clearInterval(timer)
  timer = window.setInterval(() => {
    if (authed.value) void Promise.all([loadTasks(), loadBatchJobs(), loadLogs(), loadFiles()])
  }, 1500)
}

async function loadTasks() {
  tasks.value = await api<Task[]>('/api/tasks')
}

async function loadBatchJobs() {
  batchJobs.value = await api<BatchJob[]>('/api/batch-jobs')
}

async function loadSettings() {
  settings.value = await api<Record<string, any>>('/api/settings')
}

async function saveSettings() {
  settings.value = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ values: settings.value }) })
  message.value = '设置已保存'
}

async function loadAccount() {
  account.value = await api('/api/bilibili/account')
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
  message.value = ''
  parsed.value = await api<ParsedResult>('/api/parse', { method: 'POST', body: JSON.stringify({ url: parseUrl.value }) })
  selected.value = new Set(parsed.value.episodes.map((ep) => `${ep.bvid}:${ep.cid}`))
}

function toggle(ep: Episode) {
  const key = `${ep.bvid}:${ep.cid}`
  const next = new Set(selected.value)
  next.has(key) ? next.delete(key) : next.add(key)
  selected.value = next
}

async function createTasks() {
  await api('/api/tasks', {
    method: 'POST',
    body: JSON.stringify({ episodes: selectedEpisodes.value, options: { source: parseUrl.value } })
  })
  active.value = 'tasks'
  await loadTasks()
}

async function createBatchJob() {
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
  message.value = `慢速批量下载已创建：${job.id.slice(0, 8)}`
  active.value = 'tasks'
  await Promise.all([loadTasks(), loadBatchJobs()])
}

async function batchAction(job: BatchJob, action: 'pause' | 'resume' | 'cancel') {
  await api(`/api/batch-jobs/${job.id}/${action}`, { method: 'POST', body: '{}' })
  await loadBatchJobs()
}

async function taskAction(task: Task, action: 'pause' | 'resume' | 'cancel') {
  await api(`/api/tasks/${task.id}/${action}`, { method: 'POST', body: '{}' })
  await loadTasks()
}

async function loadLogs() {
  logs.value = await api('/api/logs')
}

async function loadFiles() {
  files.value = await api('/api/files')
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
        <span>{{ message }}</span>
      </header>

      <section v-if="active === 'parse'" class="pane">
        <div class="url-row">
          <input v-model="parseUrl" placeholder="粘贴 BV 视频链接或 B 站个人主页链接" @keyup.enter="parse" />
          <button @click="parse">解析</button>
        </div>
        <div v-if="parsed" class="parsed">
          <img :src="parsed.cover" alt="" />
          <div>
            <h2>{{ parsed.title }}</h2>
            <p>
              {{ parsed.uploader }}
              <template v-if="parsed.bvid"> · {{ parsed.bvid }}</template>
              <template v-if="parsed.pagination"> · 第 {{ parsed.pagination.current_page }}/{{ parsed.pagination.total_pages }} 页，共 {{ parsed.pagination.total_items }} 个投稿</template>
            </p>
            <button @click="createTasks">创建下载任务（{{ selectedEpisodes.length }}）</button>
            <button @click="createBatchJob">慢速批量下载</button>
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
                  <button @click="batchAction(job, 'pause')">暂停</button>
                  <button @click="batchAction(job, 'resume')">继续</button>
                  <button @click="batchAction(job, 'cancel')">取消</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <table>
          <thead><tr><th>标题</th><th>状态</th><th>进度</th><th>速度</th><th>输出</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="task in tasks" :key="task.id">
              <td>{{ task.title }}<small>{{ task.error }}</small></td>
              <td><span class="badge">{{ task.status }}</span></td>
              <td><progress :value="task.progress" max="100"></progress> {{ task.progress.toFixed(1) }}%</td>
              <td>{{ formatBytes(task.speed) }}/s</td>
              <td>{{ task.output_file || task.output_dir }}</td>
              <td>
                <button @click="taskAction(task, 'pause')">暂停</button>
                <button @click="taskAction(task, 'resume')">继续</button>
                <button @click="taskAction(task, 'cancel')">取消</button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="active === 'account'" class="pane account">
        <div class="account-card">
          <img v-if="account.face" :src="account.face" alt="" />
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
