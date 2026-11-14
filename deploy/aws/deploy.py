#!/usr/bin/env python3
"""Create a fixture-only AWS demo using the installed AWS CLI and its profile.

Requires Python 3, AWS CLI, curl and a published source commit. Creates one
t3.small EC2, encrypted 30 GiB gp3 disk, Elastic IP and CloudFront distribution.
Existing instances are never modified. Credentials are never read or copied.
"""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def template(revision: str) -> dict:
    ref = lambda name: {"Ref": name}
    attr = lambda name, key: {"Fn::GetAtt": [name, key]}
    user_data = r'''#!/bin/bash
set -euo pipefail
dnf install -y docker aws-cfn-bootstrap
trap '/opt/aws/bin/cfn-signal -e $? --stack ${AWS::StackName} --resource Instance --region ${AWS::Region}' EXIT
systemctl enable --now docker amazon-ssm-agent
mkdir -p /usr/local/lib/docker/cli-plugins /opt/weatherwear
cd /usr/local/lib/docker/cli-plugins
curl -fL --retry 3 https://github.com/docker/compose/releases/download/v2.35.1/docker-compose-linux-x86_64 -o docker-compose-linux-x86_64
curl -fL --retry 3 https://github.com/docker/compose/releases/download/v2.35.1/docker-compose-linux-x86_64.sha256 -o compose.sha256
sha256sum -c compose.sha256
mv docker-compose-linux-x86_64 docker-compose
chmod 755 docker-compose
curl -fL --retry 3 https://codeload.github.com/jgoneit/WeatherWear/tar.gz/SOURCE_REVISION -o /tmp/weatherwear.tar.gz
tar -xzf /tmp/weatherwear.tar.gz -C /opt/weatherwear --strip-components=1
cd /opt/weatherwear
docker compose -f deploy/aws/compose.yaml up --build --detach --wait --wait-timeout 240
curl --fail --retry 5 --retry-connrefused http://127.0.0.1/api/recommendations/today -o /tmp/weatherwear-ready.json
python3 -c 'import json; r=json.load(open("/tmp/weatherwear-ready.json")); assert r["status"]=="ok" and len(r["items"])==5'
'''.replace("SOURCE_REVISION", revision)
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "WeatherWear public fixture demo: EC2 + HTTPS CloudFront, generated photos only",
        "Parameters": {
            "VpcId": {"Type": "AWS::EC2::VPC::Id"},
            "SubnetId": {"Type": "AWS::EC2::Subnet::Id"},
            "CloudFrontPrefixList": {"Type": "String", "AllowedPattern": "pl-[a-z0-9]+"},
            "ImageId": {
                "Type": "AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>",
                "Default": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
            },
        },
        "Resources": {
            "Role": {
                "Type": "AWS::IAM::Role",
                "Properties": {
                    "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                        "Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }]},
                    "ManagedPolicyArns": ["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"],
                },
            },
            "Profile": {"Type": "AWS::IAM::InstanceProfile", "Properties": {"Roles": [ref("Role")]}},
            "SecurityGroup": {
                "Type": "AWS::EC2::SecurityGroup",
                "Properties": {
                    "GroupDescription": "Only CloudFront can reach the demo HTTP origin; use SSM for administration",
                    "VpcId": ref("VpcId"),
                    "SecurityGroupIngress": [{
                        "IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
                        "SourcePrefixListId": ref("CloudFrontPrefixList"),
                    }],
                },
            },
            "Instance": {
                "Type": "AWS::EC2::Instance",
                "CreationPolicy": {"ResourceSignal": {"Count": 1, "Timeout": "PT25M"}},
                "Properties": {
                    "ImageId": ref("ImageId"), "InstanceType": "t3.small",
                    "IamInstanceProfile": ref("Profile"),
                    "CreditSpecification": {"CPUCredits": "standard"},
                    "MetadataOptions": {"HttpTokens": "required", "HttpPutResponseHopLimit": 1},
                    "NetworkInterfaces": [{
                        "DeviceIndex": "0", "AssociatePublicIpAddress": True,
                        "SubnetId": ref("SubnetId"), "GroupSet": [ref("SecurityGroup")],
                    }],
                    "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {
                        "VolumeSize": 30, "VolumeType": "gp3", "Encrypted": True, "DeleteOnTermination": True,
                    }}],
                    "Tags": [{"Key": "Name", "Value": ref("AWS::StackName")},
                             {"Key": "SourceRevision", "Value": revision}],
                    "UserData": {"Fn::Base64": {"Fn::Sub": user_data}},
                },
            },
            "Ip": {"Type": "AWS::EC2::EIP", "Properties": {"Domain": "vpc"}},
            "IpAssociation": {"Type": "AWS::EC2::EIPAssociation", "Properties": {
                "AllocationId": attr("Ip", "AllocationId"), "InstanceId": ref("Instance"),
            }},
            "Forwarding": {"Type": "AWS::CloudFront::OriginRequestPolicy", "Properties": {
                "OriginRequestPolicyConfig": {
                    "Name": {"Fn::Sub": "${AWS::StackName}-${AWS::Region}-requests"},
                    "CookiesConfig": {"CookieBehavior": "none"},
                    "QueryStringsConfig": {"QueryStringBehavior": "all"},
                    "HeadersConfig": {"HeaderBehavior": "whitelist", "Headers": ["Content-Type", "Origin"]},
                },
            }},
            "Distribution": {
                "Type": "AWS::CloudFront::Distribution", "DependsOn": "IpAssociation",
                "Properties": {"DistributionConfig": {
                    "Enabled": True, "Comment": ref("AWS::StackName"), "PriceClass": "PriceClass_200",
                    "HttpVersion": "http2", "IPV6Enabled": True,
                    "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
                    "Origins": [{"Id": "demo", "DomainName": attr("Instance", "PublicDnsName"),
                                 "CustomOriginConfig": {"HTTPPort": 80, "OriginProtocolPolicy": "http-only"}}],
                    "DefaultCacheBehavior": {
                        "TargetOriginId": "demo", "ViewerProtocolPolicy": "redirect-to-https", "Compress": True,
                        "AllowedMethods": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                        "CachedMethods": ["GET", "HEAD"],
                        "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
                        "OriginRequestPolicyId": ref("Forwarding"),
                    },
                }},
            },
        },
        "Outputs": {
            "Url": {"Value": {"Fn::Sub": "https://${Distribution.DomainName}"}},
            "InstanceId": {"Value": ref("Instance")},
            "SourceRevision": {"Value": revision},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--profile")
    parser.add_argument("--stack", default="weatherwear-demo")
    parser.add_argument("--vpc-id")
    parser.add_argument("--subnet-id")
    parser.add_argument("--revision", help="published full commit SHA; defaults to local HEAD")
    parser.add_argument("--render-only", type=Path, help="write template without AWS calls or creating resources")
    args = parser.parse_args()
    revision = args.revision or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise SystemExit("Use a full commit SHA for --revision")
    doc = template(revision)
    if args.render_only:
        args.render_only.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print(f"Rendered {args.render_only}; no AWS calls made")
        return
    command = ["aws", "--no-cli-pager", "--region", args.region]
    if args.profile:
        command += ["--profile", args.profile]

    def aws(*parts):
        return json.loads(subprocess.check_output([*command, *parts, "--output", "json"], text=True))

    # Fail before any resource creation if credentials, subnet or source are invalid.
    identity = aws("sts", "get-caller-identity")
    print(f"Account {identity['Account']} / region {args.region} / stack {args.stack}", flush=True)
    if not args.revision and (subprocess.call(["git", "diff", "--quiet"], cwd=ROOT) or
                              subprocess.call(["git", "diff", "--cached", "--quiet"], cwd=ROOT)):
        raise SystemExit("Commit and publish deployment changes before deploying")
    subprocess.run(["curl", "--fail", "--silent", "--show-error", "--head", "--location", "--output", "/dev/null",
                    f"https://raw.githubusercontent.com/jgoneit/WeatherWear/{revision}/deploy/aws/compose.yaml"], check=True)
    if args.vpc_id:
        vpc_id = args.vpc_id
    else:
        vpcs = aws("ec2", "describe-vpcs", "--filters", "Name=is-default,Values=true")["Vpcs"]
        if not vpcs:
            raise SystemExit("No default VPC; provide --vpc-id and a public --subnet-id")
        vpc_id = vpcs[0]["VpcId"]
    filters = [f"Name=vpc-id,Values={vpc_id}"]
    if args.subnet_id:
        filters.append(f"Name=subnet-id,Values={args.subnet_id}")
    else:
        filters.append("Name=default-for-az,Values=true")
    subnets = aws("ec2", "describe-subnets", "--filters", *filters)["Subnets"]
    if not subnets:
        raise SystemExit("No matching public subnet; provide --subnet-id")
    subnet_id = sorted(subnets, key=lambda s: s["AvailabilityZone"])[0]["SubnetId"]
    lists = aws("ec2", "describe-managed-prefix-lists", "--filters",
                "Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing")["PrefixLists"]
    if not lists:
        raise SystemExit("CloudFront origin prefix list not found")
    with tempfile.TemporaryDirectory(prefix="weatherwear-deploy-") as temp:
        path = Path(temp) / "stack.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        aws("cloudformation", "validate-template", "--template-body", f"file://{path}")
        params = [f"ParameterKey=VpcId,ParameterValue={vpc_id}",
                  f"ParameterKey=SubnetId,ParameterValue={subnet_id}",
                  f"ParameterKey=CloudFrontPrefixList,ParameterValue={lists[0]['PrefixListId']}"]
        print("Creating paid EC2, disk, IPv4 and CloudFront resources. Existing stacks are not modified.", flush=True)
        created = aws("cloudformation", "create-stack", "--stack-name", args.stack,
                      "--template-body", f"file://{path}", "--parameters", *params,
                      "--capabilities", "CAPABILITY_IAM", "--on-failure", "DELETE",
                      "--tags", "Key=Application,Value=WeatherWear", "Key=Purpose,Value=Demo")
        print(f"Stack created: {created['StackId']}. Waiting for application and HTTPS distribution…", flush=True)
    subprocess.run([*command, "cloudformation", "wait", "stack-create-complete", "--stack-name", args.stack], check=True)
    stack = aws("cloudformation", "describe-stacks", "--stack-name", args.stack)["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}
    result = subprocess.check_output(["curl", "--fail", "--silent", "--show-error", "--retry", "10",
                                      "--retry-all-errors", "--retry-delay", "5", "--max-time", "20",
                                      outputs["Url"] + "/api/recommendations/today"], text=True)
    recommendations = json.loads(result)
    if recommendations.get("status") != "ok" or len(recommendations.get("items", [])) != 5:
        raise SystemExit("Stack exists but recommendation smoke check failed; inspect it before retrying")
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
