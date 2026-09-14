import { useEffect, useState } from 'react'
import { Card, Col, Row, Statistic, Tag, Spin, Typography, Space } from 'antd'
import {
  UserOutlined,
  AppstoreOutlined,
  BookOutlined,
  ApiOutlined,
  CheckCircleTwoTone,
  WarningTwoTone,
  DatabaseOutlined,
} from '@ant-design/icons'
import { llmApi, monitorApi, ragApi, userApi } from '../api'

const { Title, Text } = Typography

export default function Dashboard() {
  const [loading, setLoading] = useState(true)
  const [users, setUsers] = useState<{ total: number }>({ total: 0 })
  const [rag, setRag] = useState<{ total_docs: number; total_chunks: number; index_size: number; has_llm: boolean; fallback_embedding: boolean }>({
    total_docs: 0, total_chunks: 0, index_size: 0, has_llm: false, fallback_embedding: false,
  })
  const [llm, setLlm] = useState<{ call_count: number; providers: string[]; default_provider: string } | null>(null)
  const [readyz, setReadyz] = useState<any>(null)

  useEffect(() => {
    Promise.all([
      userApi.list({ page: 1, page_size: 1 }).then(setUsers).catch(() => setUsers({ total: 0 })),
      ragApi.stats().then(setRag).catch(() => {}),
      llmApi.stats().then(setLlm).catch(() => {}),
      monitorApi.readyz().then(setReadyz).catch(() => {}),
    ]).finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    )
  }

  const dbUp = readyz?.components?.find((c: any) => c.name === 'database')?.status === 'up'
  const redisUp = readyz?.components?.find((c: any) => c.name === 'redis')?.status === 'up'

  return (
    <div>
      <Title level={4} style={{ marginTop: 0 }}>系统概览</Title>
      <Row gutter={16}>
        <Col xs={12} md={6}>
          <Card><Statistic title="用户总数" value={users.total} prefix={<UserOutlined />} /></Card>
        </Col>
        <Col xs={12} md={6}>
          <Card><Statistic title="知识库文档" value={rag.total_docs} prefix={<BookOutlined />} suffix={`/ ${rag.total_chunks} 分块`} /></Card>
        </Col>
        <Col xs={12} md={6}>
          <Card><Statistic title="向量索引" value={rag.index_size} prefix={<DatabaseOutlined />} suffix="chunks" /></Card>
        </Col>
        <Col xs={12} md={6}>
          <Card><Statistic title="LLM 调用次数" value={llm?.call_count ?? 0} prefix={<ApiOutlined />} /></Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col xs={24} md={12}>
          <Card title="组件状态">
            <Space direction="vertical" style={{ width: '100%' }}>
              <div>
                <Text>数据库：</Text>
                {dbUp ? <Tag icon={<CheckCircleTwoTone twoToneColor="#52c41a" />} color="success">UP</Tag>
                     : <Tag icon={<WarningTwoTone twoToneColor="#ff4d4f" />} color="error">DOWN</Tag>}
              </div>
              <div>
                <Text>Redis：</Text>
                {redisUp ? <Tag icon={<CheckCircleTwoTone twoToneColor="#52c41a" />} color="success">UP</Tag>
                        : <Tag icon={<WarningTwoTone twoToneColor="#ff4d4f" />} color="error">DOWN</Tag>}
              </div>
              <div>
                <Text>LLM：</Text>
                {rag.has_llm ? (
                  <Tag color="success">{llm?.default_provider || 'default'} · {llm?.providers?.length || 0} providers</Tag>
                ) : (
                  <Tag color="warning">未配置（search 走伪向量降级）</Tag>
                )}
              </div>
              <div>
                <Text>Embedding：</Text>
                {rag.fallback_embedding
                  ? <Tag color="warning">伪向量降级（未配置 embedding provider）</Tag>
                  : <Tag color="success">真实向量</Tag>}
              </div>
            </Space>
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card title="快速信息">
            <p><Text strong>版本：</Text> FastAPI AI Starter v2.0.0</p>
            <p><Text strong>API 前缀：</Text> <Text code>/api/sample</Text></p>
            <p><Text strong>Swagger：</Text> <a href="/api/sample/docs" target="_blank" rel="noreferrer">/api/sample/docs</a></p>
            <p><Text strong>Admin 根路径：</Text> <Text code>/admin/</Text></p>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
