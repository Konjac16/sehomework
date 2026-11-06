import { defineOverridesPreferences, definePreferencesExtension } from '@vben/preferences';
export const overridesPreferences = defineOverridesPreferences({
 app: { name:'VP Lab', defaultHomePath:'/overview', enableCheckUpdates:false, enablePreferences:false },
 theme: { mode:'light', colorPrimary:'hsl(212 100% 45%)' },
 transition: { enable:false }, copyright: { enable:false }, tabbar: { keepAlive:false },
 widget: { notification:false, languageToggle:false, lockScreen:false, themeToggle:false, timezone:false },
 logo: { source:'/vp-logo.svg' },
});
export const preferencesExtension=definePreferencesExtension({tabLabel:'工作台',fields:[]});
