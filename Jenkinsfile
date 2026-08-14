def requireRepositoryCredentialsId(String raw) {
    def credentialsId = (raw ?: '').trim()
    if (!credentialsId ||
        credentialsId ==~ /(?i).*(REPLACE|PLACEHOLDER|CHANGE_ME|CHANGEME|TODO).*/ ||
        !(credentialsId ==~ /^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$/)) {
        error('Repository SSH credentials are not configured correctly')
    }
    return credentialsId
}

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
        PIPELINE_CONTRACT = 'deeptutor-coding-v3-native-push-uat-gate-no-deploy'
    }

    stages {
        stage('Checkout canonical GitHub source') {
            steps {
                deleteDir()
                script {
                    def credentialsId = requireRepositoryCredentialsId(env.DEEPTUTOR_GITHUB_SSH_CREDENTIALS_ID)
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
                    checkout([
                        $class: 'GitSCM',
                        branches: [[name: env.SOURCE_COMMIT]],
                        userRemoteConfigs: [[
                            url: env.GIT_REPO_URL,
                            credentialsId: credentialsId,
                            refspec: "+${env.SOURCE_REF}:refs/remotes/origin/reviewed"
                        ]],
                        extensions: [
                            [$class: 'CloneOption', noTags: true, shallow: false, honorRefspec: true],
                            [$class: 'CleanBeforeCheckout']
                        ]
                    ])
                }
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
                    git merge-base --is-ancestor \
                        "$SOURCE_COMMIT" refs/remotes/origin/reviewed
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
                        python:3.11-bookworm \
                        sh -c 'owner="$(stat -c "%u:%g" /workspace)"; trap "chown -R $owner /workspace" EXIT; git --version; python -m pip install -q -r extensions/test-partner/requirements-dev.txt; cd extensions/test-partner; python -m pytest -q --no-header -k "not test_ui_track_real_browser" --junitxml=/workspace/ci-artifacts/test-partner.xml'
                    docker run --rm \
                        -v "$PWD:/workspace" \
                        -w /workspace \
                        -e PLAYWRIGHT_BROWSERS_PATH=/tmp/ms-playwright \
                        python:3.11-bookworm \
                        sh -c 'owner="$(stat -c "%u:%g" /workspace)"; trap "chown -R $owner /workspace" EXIT; printf "Acquire::Retries \"5\";\nAcquire::https::Timeout \"30\";\n" > /etc/apt/apt.conf.d/80-ci-retries; sed -i "s|http://deb.debian.org|https://deb.debian.org|g" /etc/apt/sources.list.d/debian.sources; python -m pip install -q -r extensions/test-partner/requirements-dev.txt; n=0; until python -m playwright install --with-deps chromium; do n=$((n+1)); test "$n" -lt 3; done; cd extensions/test-partner; python -m pytest -q --no-header tests/test_journey_exec.py::test_ui_track_real_browser --junitxml=/workspace/ci-artifacts/test-partner-browser.xml'
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
