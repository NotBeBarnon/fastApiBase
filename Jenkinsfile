pipeline {
    agent {
        docker {
            image 'python:3.10-slim'
            args '-v /var/run/docker.sock:/var/run/docker.sock --pull never'   // 本地有就不再 pull
        }
    }

    environment {
        // 镜像名（不含 tag）
        IMAGE_NAME = 'frp.z33.fun:23827/fastapi_cicd'
        CONTAINER_NAME = "fastapi_cicd"
        REGISTRY   = 'http://frp.z33.fun:23827'

        // 凭据 ID（与 Jenkins 全局凭据保持一致）
        REGISTRY_CREDS = credentials('registry-auth')   // Username/Password
        SSH_CREDS      = 'prod-ssh'                     // SSH Username with private key
        DEPLOY_HOST    = 'frp.z33.fun'
        DEPLOY_PORT    = '23822'
        DEPLOY_USER    = 'bistu'
        APP_PORT       = '8099'
        DOCKER_PORT    = '8089'
    }

    stages {
        /* ---------- 1. 拉取源码 ---------- */
        stage('Checkout') {
            steps {
                checkout scm

            }
        }

        /* ---------- 2. 单元测试 ---------- */
        stage('Test') {
            steps {
                sh 'python3 -m pip install --upgrade pip'
                sh 'python3 -m pip install -r requirements.txt'
//                 sh 'pytest app/ -v'  // 确保测试运行
            }
        }
    }

}
