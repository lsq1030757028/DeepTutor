pipeline {
    agent any

    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timestamps()
    }

    parameters {
        string(name: 'GITHUB_REF', defaultValue: '', description: 'Reviewed GitHub branch ref, e.g. refs/heads/feat/x')
        string(name: 'GITHUB_COMMIT', defaultValue: '', description: 'Reviewed full 40-char GitHub SHA')
    }

    environment {
        GIT_REPO_URL = 'git@github.com:lsq1030757028/DeepTutor.git'
        CREDENTIALS_ID = 'REPLACE_WITH_CODING_SSH_CREDENTIAL_ID'
        PIPELINE_CONTRACT = 'deeptutor-coding-v2-uat-gate-no-deploy'
    }

    stages {
        stage('Checkout canonical GitHub source') {
            steps {
                deleteDir()
                script {
                    if (!(env.GITHUB_REF ==~ /^refs\/heads\/[A-Za-z0-9._\/-]+$/)) {
                        error('GITHUB_REF must be a safe refs/heads branch ref')
                    }
                    if (!(env.GITHUB_COMMIT ==~ /^[0-9a-f]{40}$/)) {
                        error('GITHUB_COMMIT must be a full lowercase 40-character SHA')
                    }
                }
                checkout([
                    $class: 'GitSCM',
                    branches: [[name: env.GITHUB_COMMIT]],
                    userRemoteConfigs: [[
                        url: env.GIT_REPO_URL,
                        credentialsId: env.CREDENTIALS_ID,
                        refspec: "+${env.GITHUB_REF}:refs/remotes/origin/reviewed"
                    ]],
                    extensions: [
                        [$class: 'CloneOption', noTags: true, shallow: false, honorRefspec: true],
                        [$class: 'CleanBeforeCheckout']
                    ]
                ])
                sh '''
                    echo "pipeline_contract=$PIPELINE_CONTRACT"
                    python3 scripts/verify_coding_source.py \
                        --repo . \
                        --expected-ref "$GITHUB_REF" \
                        --expected-commit "$GITHUB_COMMIT"
                    git merge-base --is-ancestor \
                        "$GITHUB_COMMIT" refs/remotes/origin/reviewed
                '''
            }
        }

        stage('test-partner regression') {
            steps {
                sh '''
                    set -eu
                    mkdir -p ci-artifacts
                    docker run --rm \
                        -v "$PWD:/workspace" \
                        -w /workspace \
                        python:3.11-slim \
                        sh -c 'owner="$(stat -c "%u:%g" /workspace)"; trap "chown -R $owner /workspace" EXIT; python -m pip install -q -r extensions/test-partner/requirements-dev.txt; cd extensions/test-partner; python -m pytest -q --no-header --junitxml=/workspace/ci-artifacts/test-partner.xml'
                '''
            }
        }

        stage('Linux root regression') {
            steps {
                sh '''
                    set -eu
                    docker run --rm \
                        -v "$PWD:/workspace" \
                        -w /workspace \
                        -e PYTHONPATH=/workspace \
                        python:3.11 \
                        sh -c 'owner="$(stat -c "%u:%g" /workspace)"; trap "chown -R $owner /workspace" EXIT; git --version; python -m pip install -q -r requirements/server.txt -r requirements/partners.txt pytest pytest-asyncio; mkdir -p data/user/settings; printf "system:\n  language: en\nlogging:\n  level: WARNING\n" > data/user/settings/main.yaml; python -m pytest -q tests deeptutor/learning/tests --junitxml=ci-artifacts/root-linux.xml'
                '''
            }
        }

        stage('Web UAT gate') {
            steps {
                sh '''
                    set -eu
                    docker run --rm \
                        -v "$PWD:/workspace" \
                        -w /workspace/web \
                        -e HOME=/tmp \
                        -e NEXT_TELEMETRY_DISABLED=1 \
                        node:22.12.0 \
                        sh -c 'owner="$(stat -c "%u:%g" /workspace)"; trap "chown -R $owner /workspace" EXIT; npm ci --legacy-peer-deps; npm run test:node; npm run i18n:parity; npm run build; npm run perf:check'
                '''
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'ci-artifacts/*.xml', allowEmptyArchive: true
        }
    }
}
