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
        PIPELINE_CONTRACT = 'deeptutor-coding-v1-no-deploy'
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
                '''
            }
        }

        stage('Required local-equivalent tests') {
            steps {
                script {
                    docker.image('python:3.11-slim').inside {
                        sh '''
                            set -eu
                            python -m pip install -q -r extensions/test-partner/requirements-dev.txt
                            cd extensions/test-partner
                            python -m pytest -q --no-header
                        '''
                    }
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'extensions/test-partner/**/pytest*.xml', allowEmptyArchive: true
        }
    }
}
