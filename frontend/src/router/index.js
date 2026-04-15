import { createRouter, createWebHistory } from 'vue-router'
import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'
import ChatView from '../views/ChatView.vue'
import { ElMessage } from 'element-plus'
const routes = [
  {
    path: '/',
    redirect: '/login',
    meta:{title:"登录页"}
  },
  {
    path: '/login',
    component: LoginView
  },
  {
    path: '/register',
    component: RegisterView
  },
  {
    path: '/chat',
    component: ChatView
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})
//路由守卫
router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (to.path === '/chat' &&  token !== 'mock-token') {
    ElMessage({message: "非法访问", type: 'warning'});
    return '/login'
  } else {
    return true
  }
})
router.afterEach((to,from)=>{
})


export default router