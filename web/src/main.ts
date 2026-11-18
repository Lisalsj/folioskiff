import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { bootstrapLocalSession } from './lib/session'
import { createAppRouter } from './router'
import { readCachedLocale, setLocale } from './stores/locale'
import { applyCachedTheme } from './stores/preferences'
import './styles/base.css'

applyCachedTheme()

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)

async function start(): Promise<void> {
  await Promise.all([bootstrapLocalSession().catch(() => false), setLocale(readCachedLocale()).catch(() => {})])
  const router = createAppRouter()
  app.use(router)
  await router.isReady()
  app.mount('#app')
}

void start()
