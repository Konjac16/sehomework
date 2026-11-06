import { defineConfig } from '@vben/vite-config';
export default defineConfig(async()=>({application:{},vite:{resolve:{dedupe:['vue','vue-router','pinia']},server:{proxy:{'/api':{target:'http://127.0.0.1:8765',changeOrigin:true},'/duo':{target:'http://127.0.0.1:8765',changeOrigin:true}}}}}));
