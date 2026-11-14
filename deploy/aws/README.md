# AWS fixture demo

아래 명령은 유효한 AWS CLI 자격증명으로 **유료 AWS 자원**을 생성합니다. 먼저 `get-caller-identity`로 계정을 확인하세요. `InvalidClientTokenId`가 나오면 키를 갱신해야 하며 자원 생성은 시작되지 않습니다.

## 실행

배포 코드를 커밋하고 GitHub에 push한 작업 폴더에서:

```sh
aws sts get-caller-identity --region ap-northeast-2
python3 deploy/aws/deploy.py --region ap-northeast-2
```

다른 CLI 프로필은 `--profile NAME`, 스택 이름은 `--stack NAME`으로 지정합니다. 기본 VPC/서브넷이 없는 계정은 인터넷 게이트웨이로 연결되는 공개 서브넷을 `--vpc-id VPC --subnet-id SUBNET`으로 지정해야 합니다. VPC의 DNS hostnames/support가 활성화되어 있어야 합니다.

스크립트는 AWS CLI로 인증·VPC·서브넷·CloudFront prefix list를 조회하고, 템플릿을 검증한 뒤 새 CloudFormation 스택을 만듭니다. 이미 있는 스택·서버는 수정하지 않습니다. 새로 만드는 IAM 역할에 대한 `CAPABILITY_IAM`을 사용하며 EC2, IAM, CloudFormation, CloudFront, SSM 파라미터 조회 권한이 필요합니다.

스택이 준비되면 HTTPS URL과 인스턴스 ID를 출력합니다. 애플리케이션 초기화에 실패한 신규 스택은 자동 삭제하도록 설정했습니다. 실행 도중 터미널을 닫아도 스택 생성은 계속되므로 다시 실행하기 전에 `aws cloudformation describe-stacks --stack-name weatherwear-demo --region ap-northeast-2`로 상태를 확인하세요.

## 구성과 범위

- 서울 리전 기본, EC2 `t3.small` 1대, 암호화한 gp3 30GiB, Elastic IP, CloudFront. 실행 시간·디스크·IPv4·트래픽 비용이 발생합니다. CPU 크레딧은 standard입니다.
- CloudFront 기본 도메인의 HTTPS를 사용합니다. CloudFront→EC2 origin 구간은 HTTP이고 보안 그룹에서 CloudFront origin IP만 허용합니다. 계정 로그인이나 민감 정보 입력을 받는 운영 서비스용 구성은 아닙니다.
- SSH/백엔드 포트는 공개하지 않습니다. 관리는 Systems Manager Session Manager를 사용합니다.
- 서버가 GitHub의 정확한 commit SHA를 내려받습니다. 저장소에 포함된 생성 착장 15장만 배포되며 로컬 개인 사진·`.env`·API 키는 업로드하지 않습니다. Gemini 호출 없이 fixture만 사용합니다.
- `/api`는 같은 출처로 프록시합니다. 개발용 관리 화면은 비활성화하고 스캔 등 `/api/demo` 관리 요청은 차단합니다. 공개 방문자는 날씨 선택과 체감 피드백만 변경할 수 있습니다.
- 첫 시작 및 재시작 때 스캔을 수행하고 30°C 시나리오를 선택합니다. 사진 중복 저장은 방지합니다. 세 날씨는 홈에서 선택할 수 있습니다.
- SQLite와 피드백은 Docker volume에 저장돼 컨테이너 재시작 후에도 유지됩니다. **스택/인스턴스 삭제 시 사라집니다.** 다중 사용자 로그인·사용자별 분리·자동 백업·다중 인스턴스는 구현하지 않았습니다. 시나리오와 피드백은 데모 방문자 전체가 공유합니다.

## 검증과 관리

클라우드 자원을 만들지 않고 템플릿 생성:

```sh
python3 deploy/aws/deploy.py --render-only /tmp/weatherwear-stack.json
```

로컬 Docker 확인(로컬 photos 폴더에 개인 사진이 있으면 이 로컬 테스트에서만 함께 표시됨):

```sh
WEB_PORT=18080 docker compose -f deploy/aws/compose.yaml up --build --wait
curl http://localhost:18080/api/recommendations/today
curl -i -X POST http://localhost:18080/api/demo/scan  # 404여야 함
```

AWS 콘솔에서 스택 이벤트와 `/var/log/cloud-init-output.log`를 확인할 수 있습니다. 인스턴스 ID로 SSM 세션을 열거나 Run Command를 사용하세요. 기존 스택 업데이트는 이 최초 배포 스크립트가 자동 처리하지 않습니다.

데모 종료 후 자원 삭제(데이터도 삭제됨):

```sh
aws cloudformation delete-stack --stack-name weatherwear-demo --region ap-northeast-2
aws cloudformation wait stack-delete-complete --stack-name weatherwear-demo --region ap-northeast-2
```
