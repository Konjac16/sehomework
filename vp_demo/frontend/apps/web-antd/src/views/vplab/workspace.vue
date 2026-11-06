<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { mountWorkspace } from './workspace';
const root=ref<HTMLElement>(); const route=useRoute(); const router=useRouter(); const error=ref('');
let navigate:((tab:string)=>void)|undefined;
const onNavigation=(event:Event)=>{const tab=(event as CustomEvent).detail;if(route.name!==tab)void router.push('/'+tab);};
onMounted(async()=>{window.addEventListener('vp:navigate',onNavigation);try{navigate=await mountWorkspace(root.value!);navigate(String(route.name));}catch(e){error.value=String(e);}});
watch(()=>route.name,name=>navigate?.(String(name)));
onBeforeUnmount(()=>window.removeEventListener('vp:navigate',onNavigation));
</script>
<template><div class="p-5"><div v-if="error" role="alert">{{ error }}</div><div ref="root" /></div></template>
