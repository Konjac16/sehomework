import { createApp } from 'vue';
import { initStores, useAccessStore } from '@vben/stores';
import '@vben/styles';
import '@vben/styles/antd';
import { setupI18n } from '#/locales';
import App from './app.vue';
import { router, menus } from './router';
export async function bootstrap(namespace:string) {
 const app=createApp(App); await setupI18n(app); await initStores(app,{namespace});
 useAccessStore().setAccessMenus(menus); app.use(router); app.mount('#app');
}
