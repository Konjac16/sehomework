import { markRaw } from 'vue';
import { createRouter, createWebHashHistory } from 'vue-router';
import { LayoutGrid, SquareCode, Copy, ListOrdered, ArrowRightLeft, BookOpenText, SquareCheckBig, InspectionPanel, Grid, Settings } from '@vben/icons';
import Layout from '#/layouts/basic.vue';
import Workspace from '#/views/vplab/workspace.vue';
const destinations = [
  { name: 'overview', title: '训练概览', icon: LayoutGrid },
  { name: 'training', title: '日常训练', icon: SquareCode },
  { name: 'intelligent', title: '智能推荐', icon: InspectionPanel },
  { name: 'problems', title: '题目与代码', icon: Copy },
  { name: 'history', title: '提交历史', icon: ListOrdered },
  { name: 'compare', title: '代码复盘', icon: ArrowRightLeft },
  { name: 'experiences', title: '经验库', icon: BookOpenText },
  { name: 'tasks', title: '补题计划', icon: SquareCheckBig },
  { name: 'portrait', title: '训练统计', icon: InspectionPanel },
  { name: 'predict', title: '难度估算', icon: Grid },
  { name: 'settings', title: '数据与账号', icon: Settings },
];
export const menus = destinations.map(({ name, title, icon }) => ({ name: title, path: `/${name}`, icon: markRaw(icon) }));
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [{ path: '/', component: Layout, redirect: '/overview', children: destinations.map(({ name, title, icon }) => ({
    path: name, name, component: Workspace, meta: { title, icon: markRaw(icon), affixTab: name === 'overview' },
  })) }],
});
export const resetRoutes = () => {};
