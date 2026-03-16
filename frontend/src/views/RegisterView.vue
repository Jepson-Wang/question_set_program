<template>
  <div class="register-page">
    <div class="register-card">
      <div class="header">
        <h1>QuestionForge</h1>
        <p>智能题目生成平台</p>
      </div>

      <el-form
        ref="formRef"
        :model="registerForm"
        :rules="rules"
        label-position="top"
        class="register-form"
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="registerForm.username"
            placeholder="请输入用户名"
            size="large"
            clearable
          />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="registerForm.password"
            type="password"
            placeholder="请输入密码"
            size="large"
            show-password
            clearable
          />
        </el-form-item>

        <el-form-item label="确认密码" prop="confirmPassword">
          <el-input
            v-model="registerForm.confirmPassword"
            type="password"
            placeholder="请再次输入密码"
            size="large"
            show-password
            clearable
            @keyup.enter="handleRegister"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            class="register-btn"
            @click="handleRegister"
          >
            注册
          </el-button>
        </el-form-item>
      </el-form>

      <div class="footer">
        <span>已有账号？</span>
        <span class="link" @click="goLogin">去登录</span>
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

const registerForm = reactive({
  username: '',
  password: '',
  confirmPassword: ''
})

const validateConfirmPassword = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请再次输入密码'))
  } else if (value !== registerForm.password) {
    callback(new Error('两次输入的密码不一致'))
  } else {
    callback()
  }
}

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度应为 3 到 20 位', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码长度不能少于 6 位', trigger: 'blur' }
  ],
  confirmPassword: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validateConfirmPassword, trigger: 'blur' }
  ]
}

const handleRegister = async () => {
  try {
    await formRef.value.validate()

    // 这里先做演示，后面再接注册接口
    ElMessage.success('注册表单校验通过')

    console.log('注册参数：', {
      username: registerForm.username,
      password: registerForm.password,
      confirmPassword: registerForm.confirmPassword
    })

    // 后面接上真实注册接口成功后，再跳转登录页
    // router.push('/login')
  } catch (error) {
    console.log('表单校验未通过', error)
  }
}

const goLogin = () => {
  router.push('/login')
}
</script>

<style scoped>
.register-page {
  min-height: 100vh;
  display: flex;
  justify-content: center;
  align-items: center;
  background: linear-gradient(135deg, #eef2ff 0%, #f8fafc 100%);
  padding: 20px;
  box-sizing: border-box;
}

.register-card {
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

.register-form {
  margin-top: 8px;
}

.register-btn {
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
  .register-card {
    padding: 24px 18px;
    border-radius: 12px;
  }

  .header h1 {
    font-size: 24px;
  }
}
</style>