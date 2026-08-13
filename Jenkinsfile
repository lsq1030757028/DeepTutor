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
        PIPELINE_CONTRACT = 'deeptutor-coding-v2-native-push-no-deploy'
    }

    stages {
        stage('Checkout canonical GitHub source') {
            steps {
                deleteDir()
                script {
                    def triggerMethod = (env.CCI_TRIGGER_METHOD ?: '').trim().toUpperCase()
                    def sourceRef
                    def sourceCommit
                    def sourceMode
                    if (triggerMethod == 'PUSH') {
                        def branch = (env.GIT_LOCAL_BRANCH ?: '').trim()
                        if ((env.GIT_TAG ?: '').trim() || branch.startsWith('refs/')) {
                            error('PUSH trigger must identify an unqualified branch, not a tag or ref')
                        }
                        sourceRef = "refs/heads/${branch}"
                        sourceCommit = (env.GIT_COMMIT ?: '').trim()
                        sourceMode = 'native-push'
                    } else if (triggerMethod in ['', 'MANUAL', 'API']) {
                        sourceRef = (params.GITHUB_REF ?: '').trim()
                        sourceCommit = (params.GITHUB_COMMIT ?: '').trim()
                        sourceMode = 'explicit-fallback'
                    } else {
                        error('Unsupported CODING trigger method')
                    }
                    if (!(sourceRef ==~ /^refs\/heads\/[A-Za-z0-9][A-Za-z0-9._\/-]*$/) ||
                        sourceRef.contains('..') || sourceRef.contains('//') ||
                        sourceRef.contains('/.') || sourceRef.contains('.lock/') ||
                        sourceRef.contains('@{') || sourceRef.endsWith('/') ||
                        sourceRef.endsWith('.') || sourceRef.endsWith('.lock')) {
                        error('Source ref must be a safe refs/heads branch ref')
                    }
                    if (!(sourceCommit ==~ /^[0-9a-f]{40}$/)) {
                        error('Source commit must be a full lowercase 40-character SHA')
                    }
                    env.SOURCE_REF = sourceRef
                    env.SOURCE_COMMIT = sourceCommit
                    env.SOURCE_MODE = sourceMode
                }
                checkout([
                    $class: 'GitSCM',
                    branches: [[name: env.SOURCE_COMMIT]],
                    userRemoteConfigs: [[
                        url: env.GIT_REPO_URL,
                        credentialsId: env.CREDENTIALS_ID,
                        refspec: "+${env.SOURCE_REF}:refs/remotes/origin/reviewed"
                    ]],
                    extensions: [
                        [$class: 'CloneOption', noTags: true, shallow: false, honorRefspec: true],
                        [$class: 'CleanBeforeCheckout']
                    ]
                ])
                sh '''
                    set -eu
                    echo "pipeline_contract=$PIPELINE_CONTRACT"
                    echo "source_mode=$SOURCE_MODE"
                    test "$(python3 scripts/resolve_coding_source.py --trigger-method "${CCI_TRIGGER_METHOD:-}" --git-local-branch "${GIT_LOCAL_BRANCH:-}" --git-commit "${GIT_COMMIT:-}" --git-tag "${GIT_TAG:-}" --github-ref "${GITHUB_REF:-}" --github-commit "${GITHUB_COMMIT:-}" --field ref)" = "$SOURCE_REF"
                    test "$(python3 scripts/resolve_coding_source.py --trigger-method "${CCI_TRIGGER_METHOD:-}" --git-local-branch "${GIT_LOCAL_BRANCH:-}" --git-commit "${GIT_COMMIT:-}" --git-tag "${GIT_TAG:-}" --github-ref "${GITHUB_REF:-}" --github-commit "${GITHUB_COMMIT:-}" --field commit)" = "$SOURCE_COMMIT"
                    python3 scripts/verify_coding_source.py \
                        --repo . \
                        --expected-ref "$SOURCE_REF" \
                        --expected-commit "$SOURCE_COMMIT"
                '''
            }
        }

        stage('Required local-equivalent tests') {
            steps {
                sh '''
                    set -eu
                    docker run --rm \
                        -v "$PWD:/workspace" \
                        -w /workspace \
                        python:3.11-slim \
                        sh -c 'python -m pip install -q -r extensions/test-partner/requirements-dev.txt && cd extensions/test-partner && python -m pytest -q --no-header'
                '''
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'extensions/test-partner/**/pytest*.xml', allowEmptyArchive: true
        }
    }
}
