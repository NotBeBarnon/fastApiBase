import { useEffect, useState } from 'react'
import { Table, Tag, Button, Space, message, Popconfirm, Modal, Descriptions } from 'antd'
import type { TablePaginationConfig } from 'antd/es/table'
import dayjs from 'dayjs'
import { ragApi, type Paged, type RagDoc } from '../api'

export default function KnowledgePage() {
  const [data, setData] = useState<Paged<RagDoc>>({ total: 0, page: 1, page_size: 10, items: [] })
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<RagDoc | null>(null)

  const load = async (page = 1, page_size = 10) => {
    setLoading(true)
    try {
      const r = await ragApi.listDocs({ page, page_size })
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
      await ragApi.deleteDoc(id)
      message.success('已删除')
      load(data.page, data.page_size)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '标题', dataIndex: 'title' },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 80,
      render: (v: boolean) => v ? <Tag color="success">启用</Tag> : <Tag color="default">停用</Tag>,
    },
    { title: '分块数', dataIndex: 'chunk_count', width: 90 },
    { title: 'Owner', dataIndex: 'owner_id', width: 80 },
    { title: '来源', dataIndex: 'source', width: 140, render: (s: string) => s || '-' },
    { title: '创建时间', dataIndex: 'created_at', width: 170, render: (t: string) => dayjs(t).format('YYYY-MM-DD HH:mm:ss') },
    {
      title: '操作',
      key: 'op',
      width: 160,
      render: (_: any, r: RagDoc) => (
        <Space>
          <Button size="small" onClick={() => setDetail(r)}>详情</Button>
          <Popconfirm title="确认删除文档？" onConfirm={() => remove(r.id)}>
            <Button size="small" danger>删除</Button>
          </Popconfirm>
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
    <>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={data.items}
        loading={loading}
        pagination={pagination}
      />
      <Modal
        title={detail?.title}
        open={!!detail}
        onCancel={() => setDetail(null)}
        footer={null}
        width={720}
      >
        {detail && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="ID">{detail.id}</Descriptions.Item>
            <Descriptions.Item label="Owner">{detail.owner_id}</Descriptions.Item>
            <Descriptions.Item label="分块数">{detail.chunk_count}</Descriptions.Item>
            <Descriptions.Item label="来源">{detail.source || '-'}</Descriptions.Item>
            <Descriptions.Item label="摘要">{detail.summary || '-'}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{dayjs(detail.created_at).format('YYYY-MM-DD HH:mm:ss')}</Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </>
  )
}
