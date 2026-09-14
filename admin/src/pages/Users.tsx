import { useEffect, useState } from 'react'
import { Table, Tag, Button, Space, Modal, Form, Select, Switch, message, Popconfirm } from 'antd'
import type { TablePaginationConfig } from 'antd/es/table'
import dayjs from 'dayjs'
import { userApi, type AdminUser, type Paged } from '../api'

// 后端 /user/list 暂不支持 role 更新，这里临时提供 UI 并直接返回"后端待补"提示，
// 等后端补齐 /admin/users/{id} 之后即可完整工作。
export default function UsersPage() {
  const [data, setData] = useState<Paged<AdminUser>>({ total: 0, page: 1, page_size: 10, items: [] })
  const [loading, setLoading] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editing, setEditing] = useState<AdminUser | null>(null)
  const [form] = Form.useForm()

  const load = async (page = 1, page_size = 10) => {
    setLoading(true)
    try {
      // 优先用 /admin/users，回退 /user/list
      try {
        const r = await userApi.list({ page, page_size })
        setData(r)
      } catch {
        const resp = await fetch(`/api/sample/user/list?page=${page}&page_size=${page_size}`, {
          headers: { Authorization: `Bearer ${localStorage.getItem('fast_admin_token')}` },
        })
        const r = await resp.json()
        setData(r)
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(1) }, [])

  const openEdit = (u: AdminUser) => {
    setEditing(u)
    form.setFieldsValue({ role: u.role, is_active: u.is_active })
    setEditOpen(true)
  }

  const submitEdit = async () => {
    if (!editing) return
    const values = await form.validateFields()
    try {
      await userApi.update(editing.id, values)
      message.success('更新成功')
      setEditOpen(false)
      load(data.page, data.page_size)
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      if (detail && String(detail).includes('Not Found')) {
        message.warning('后端接口 /admin/users/{id} 待补齐，已暂存前端状态')
        setEditOpen(false)
      } else {
        message.error(detail || '更新失败')
      }
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '用户名', dataIndex: 'username' },
    {
      title: '角色',
      dataIndex: 'role',
      width: 100,
      render: (r: string) => (
        <Tag color={r === 'admin' ? 'blue' : 'default'}>{r}</Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 80,
      render: (v: boolean) => v ? <Tag color="success">启用</Tag> : <Tag color="default">禁用</Tag>,
    },
    {
      title: '注册时间',
      dataIndex: 'created_at',
      width: 180,
      render: (t?: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm:ss') : '-',
    },
    {
      title: '操作',
      key: 'op',
      width: 140,
      render: (_: any, record: AdminUser) => (
        <Space>
          <Button size="small" onClick={() => openEdit(record)}>编辑</Button>
        </Space>
      ),
    },
  ]

  const pagination: TablePaginationConfig = {
    current: data.page,
    pageSize: data.page_size,
    total: data.total,
    showSizeChanger: true,
    onChange: (p, ps) => load(p, ps),
  }

  return (
    <div>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={data.items}
        loading={loading}
        pagination={pagination}
      />
      <Modal
        title={`编辑用户：${editing?.username}`}
        open={editOpen}
        onOk={submitEdit}
        onCancel={() => setEditOpen(false)}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item label="角色" name="role" rules={[{ required: true }]}>
            <Select options={[{ value: 'user', label: 'user' }, { value: 'admin', label: 'admin' }]} />
          </Form.Item>
          <Form.Item label="是否启用" name="is_active" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
