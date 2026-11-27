import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import { pages } from './nav'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    {
      path: '/dashboard',
      name: 'dashboard',
      component: () => import('./views/Dashboard.vue'),
    },
    {
      path: '/polymarket-account',
      name: 'polymarket-account',
      component: () => import('./views/PolymarketAccount.vue'),
    },
    {
      path: '/paper-accounts/:id',
      name: 'paper-account',
      component: () => import('./views/Account.vue'),
    },
    {
      path: '/markets/:id',
      name: 'market',
      component: () => import('./views/Market.vue'),
    },
    ...pages
      .filter((p) => !['dashboard', 'paper-account', 'market', 'polymarket-account'].includes(p.key))
      .map((p) => ({
        path: '/' + p.key,
        name: p.key,
        component: () => import('./views/Resource.vue'),
      })),
    {
      path: '/:pathMatch(.*)*',
      name: 'missing',
      component: () => import('./views/Missing.vue'),
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
createApp(App).use(createPinia()).use(router).mount('#app')
