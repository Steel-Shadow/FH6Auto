import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import 'vuetify/styles'
import App from './App.vue'
import './styles.css'

const vuetify = createVuetify({
  components,
  directives,
  theme: {
    defaultTheme: 'fh6Dark',
    themes: {
      fh6Dark: {
        dark: true,
        colors: {
          background: '#0b0f14',
          surface: '#151c26',
          primary: '#4da3ff',
          secondary: '#8fa6c4',
          success: '#40b896',
          warning: '#f2b84b',
          error: '#e45c64',
          info: '#4fc3d7',
        },
      },
    },
  },
})

createApp(App).use(vuetify).mount('#app')

