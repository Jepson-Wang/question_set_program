<template>
  <div class="login-page">
    <div class="login-card">
      <div class="header">
        <h1>QuestionForge</h1>
        <p>一个人工智能问题生成平台</p>
      </div>

      <el-form
        ref="formRef"
        :model="loginForm"
        :rules="rules"
        label-position="top"
        class="login-form"
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="loginForm.username"
            placeholder="请输入用户名"
            size="large"
            clearable
          />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            placeholder="请输入输入密码"
            size="large"
            show-password
            clearable
            @keyup.enter="handleLogin"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            class="login-btn"
            @click="handleLogin"
          >
            登录
          </el-button>
        </el-form-item>
      </el-form>

      <div class="footer">
        <span>没有账户</span>
        <span class="link" @click="goRegister">点我创建</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { loginApi } from '../api/auth'
import { setToken, setTokenType } from '../utils/auth'

const router = useRouter()
const formRef = ref()

// 登录表单数据
const loginForm = reactive({
  username: '',
  password: ''
})

// 表单校验规则
const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码长度不能少于 6 位', trigger: 'blur' }
  ]
}

// 点击登录
const handleLogin = async () => {
  try {
    // 先做表单校验
    await formRef.value.validate()

    // 调用登录接口
    const res = await loginApi({
      username: loginForm.username,
      password: loginForm.password
    })

    console.log('登录返回结果：', res)

    // 后端最新说会返回 access_token 和 token_type
    const accessToken = res.access_token
    const tokenType = res.token_type

    // 登录成功
    if (accessToken) {
      // 保存 token
      setToken(accessToken)

      // 保存 token_type
      setTokenType(tokenType)

      ElMessage.success('登录成功')

      // 跳转到聊天页
      router.push('/chat')
    } else {
      ElMessage.error(res.msg || '登录失败')
    }
  } catch (error) {
    console.log('登录请求失败：', error)
    ElMessage.error('请求失败，请检查后端服务是否启动')
  }
}

// 去注册页
const goRegister = () => {
  router.push('/register')
}
</script>
<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  justify-content: center;
  align-items: center;
  background: linear-gradient(135deg, #eef2ff 0%, #f8fafc 100%);
  padding: 20px;
  box-sizing: border-box;
}

.login-card {
  width: 100%;
  max-width: 420px;
  background: #fff;
  border-radius: 16px;
  padding: 32px 28px;
  box-sizing: border-box;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.08);
}

.header {
  text-align: center;
  margin-bottom: 28px;
}

.header h1 {
  margin: 0;
  font-size: 30px;
  color: #111827;
}

.header p {
  margin: 10px 0 0;
  font-size: 14px;
  color: #6b7280;
}

.login-form {
  margin-top: 8px;
}

.login-btn {
  width: 100%;
}

.footer {
  margin-top: 8px;
  text-align: center;
  font-size: 14px;
  color: #6b7280;
}

.link {
  margin-left: 6px;
  color: #409eff;
  cursor: pointer;
}

.link:hover {
  text-decoration: underline;
}

@media (max-width: 480px) {
  .login-card {
    padding: 24px 18px;
    border-radius: 12px;
  }

  .header h1 {
    font-size: 24px;
  }
}
</style>