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

const router = useRouter()
const formRef = ref()

const loginForm = reactive({
  username: '',
  password: ''
})

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' }
  ],
  password: [
    { required: true, message: 'Please enter password', trigger: 'blur' },
    { min: 6, message: '密码至少为6个字符', trigger: 'blur' }
  ]
}

const handleLogin = async () => {
  try {
    await formRef.value.validate()

    // 这里先写死演示，后面再接接口
    ElMessage.success('Login form validation passed')

    console.log('登录参数：', {
      username: loginForm.username,
      password: loginForm.password
    })

    // 后面接上接口成功后再跳转
    // router.push('/chat')
  } catch (error) {
    console.log('表单校验未通过', error)
  }
}

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