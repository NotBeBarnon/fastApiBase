import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Form, Input, Button, Card, message, Alert } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { authApi } from '../api'

export default function LoginPage() {
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const [search] = useSearchParams()
  const forbidden = search.get('forbidden')

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      const resp = await authApi.login(values.username, values.password)
      if (resp.user.role !== 'admin') {
        message.error('该账号非管理员，无权访问后台')
        authApi.logout()
        return
      }
      message.success('登录成功')
      const redirect = search.get('redirect') || '/'
      navigate(redirect, { replace: true })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg,#667eea 0%,#764ba2 100%)',
      }}
    >
      <Card
        style={{ width: 380, boxShadow: '0 8px 30px rgba(0,0,0,.12)' }}
        title={
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 20, fontWeight: 600 }}>FastAPI AI Starter</div>
            <div style={{ fontSize: 13, color: '#888', marginTop: 4 }}>管理后台</div>
          </div>
        }
        bordered={false}
      >
        {forbidden && (
          <Alert
            type="error"
            showIcon
            message="无访问权限"
            description="仅 admin 角色可访问管理后台"
            style={{ marginBottom: 16 }}
          />
        )}
        <Form layout="vertical" onFinish={onFinish} autoComplete="off" initialValues={{ username: 'admin' }}>
          <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="admin" size="large" />
          </Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" size="large" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" block size="large" loading={loading}>
              登 录
            </Button>
          </Form.Item>
        </Form>
        <div style={{ marginTop: 16, fontSize: 12, color: '#aaa', textAlign: 'center' }}>
          管理员账号需由数据库初始化（role = 'admin'）
        </div>
      </Card>
    </div>
  )
}
