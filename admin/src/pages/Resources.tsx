import { useEffect, useState } from 'react'
import { Table, Tag, Button, Space, message, Popconfirm } from 'antd'
import type { TablePaginationConfig } from 'antd/es/table'
import dayjs from 'dayjs'
import { resourceApi, type Beam, type Paged } from '../api'

const BeamTypeMap: Record<number, { label: string; color: string }> = {
  1: { label: 'KA', color: 'blue' },
  2: { label: 'X', color: 'purple' },
}

export default function ResourcesPage() {
  const [data, setData] = useState<Paged<Beam>>({ total: 0, page: 1, page_size: 10, items: [] })
  const [loading, setLoading] = useState(false)

  const load = async (page = 1, page_size = 10) => {
    setLoading(true)
    try {
      const r = await resourceApi.listBeams({ page, page_size })
      setData(r)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(1) }, [])

  const remove = async (id: number) => {
    try {
      await resourceApi.deleteBeam(id)
      message.success('已删除')
      load(data.page, data.page_size)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '名称', dataIndex: 'name' },
    {
      title: '类型',
      dataIndex: 'type',
      width: 80,
      render: (t: number) => {
        const meta = BeamTypeMap[t] || { label: `#${t}`, color: 'default' }
        return <Tag color={meta.color}>{meta.label}</Tag>
      },
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 80,
      render: (v: boolean) => v ? <Tag color="success">启用</Tag> : <Tag color="default">停用</Tag>,
    },
    { title: 'Owner ID', dataIndex: 'owner_id', width: 90 },
    { title: '创建时间', dataIndex: 'created_at', width: 170, render: (t: string) => dayjs(t).format('YYYY-MM-DD HH:mm:ss') },
    {
      title: '操作',
      key: 'op',
      width: 100,
      render: (_: any, r: Beam) => (
        <Popconfirm title="确认删除？" onConfirm={() => remove(r.id)}>
          <Button size="small" danger>删除</Button>
        </Popconfirm>
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
    <Table
      rowKey="id"
      columns={columns}
      dataSource={data.items}
      loading={loading}
      pagination={pagination}
    />
  )
}
