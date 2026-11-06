<script setup lang="ts">
import { BasicLayout } from '@vben/layouts';
import { Button } from 'ant-design-vue';
async function logout() {
  const response = await fetch('/api/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (response.ok) window.location.reload();
}
</script>
<template>
  <BasicLayout logo-text="VP Lab" @logout="logout">
    <template #user-dropdown>
      <div class="flex items-center gap-3 px-4">
        <span class="text-muted-foreground text-xs">本地训练空间</span>
        <Button href="/duo">VP 训练房</Button>
      </div>
    </template>
  </BasicLayout>
</template>
