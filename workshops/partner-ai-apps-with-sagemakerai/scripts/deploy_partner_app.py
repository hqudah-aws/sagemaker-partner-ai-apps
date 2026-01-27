#!/usr/bin/env python3
import boto3
from botocore.exceptions import ClientError
from InquirerPy import inquirer
from InquirerPy.base.control import Choice
import json
from datetime import datetime
import os

def check_iam_role(role_name, session):
    """Check if IAM role exists"""
    try:
        iam = session.client('iam')
        response = iam.get_role(RoleName=role_name)
        return True, response['Role']
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return False, None
        raise

def create_execution_role(role_name, session):
    """Create PartnerAiAppExecutionRole with required policies"""
    iam = session.client('iam')
    
    assume_role_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": ["sagemaker.amazonaws.com"]},
            "Action": "sts:AssumeRole"
        }]
    }
    
    license_manager_policy = {
        "Version": "2012-10-17",
        "Statement": {
            "Effect": "Allow",
            "Action": [
                "license-manager:CheckoutLicense",
                "license-manager:CheckInLicense",
                "license-manager:ExtendLicenseConsumption",
                "license-manager:GetLicense",
                "license-manager:GetLicenseUsage"
            ],
            "Resource": "*"
        }
    }
    
    bedrock_policy = {
        "Version": "2012-10-17",
        "Statement": {
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:GetFoundationModel",
                "bedrock:GetInferenceProfile"
            ],
            "Resource": "*"
        }
    }
    
    print(f"\nCreating role '{role_name}'...")
    iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(assume_role_policy)
    )
    
    print("Attaching LicenseManagerPolicy...")
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName='LicenseManagerPolicy',
        PolicyDocument=json.dumps(license_manager_policy)
    )
    
    print("Attaching BedrockInferencePolicy...")
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName='BedrockInferencePolicy',
        PolicyDocument=json.dumps(bedrock_policy)
    )
    
    role = iam.get_role(RoleName=role_name)['Role']
    print(f"✓ Role created successfully")
    print(f"  ARN: {role['Arn']}")
    return role

def get_available_profiles():
    """Get list of configured AWS profiles"""
    profiles = ['default']
    
    aws_config_file = os.path.expanduser('~/.aws/credentials')
    if os.path.exists(aws_config_file):
        with open(aws_config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    profile = line[1:-1]
                    if profile != 'default' and profile not in profiles:
                        profiles.append(profile)
    
    return profiles

def select_aws_profile():
    """Let user select AWS profile"""
    profiles = get_available_profiles()
    
    if len(profiles) == 1:
        print(f"\nUsing AWS profile: default")
        return None
    
    selected = inquirer.select(
        message="Select AWS profile:",
        choices=profiles,
        default='default'
    ).execute()
    
    return None if selected == 'default' else selected

def validate_credentials(profile=None):
    """Validate AWS credentials and return caller identity"""
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        sts = session.client('sts')
        identity = sts.get_caller_identity()
        print("\n✓ AWS Credentials validated")
        print(f"  Account: {identity['Account']}")
        print(f"  User/Role: {identity['Arn']}")
        return identity, session
    except Exception as e:
        print(f"\n✗ Failed to validate AWS credentials: {e}")
        print("  Please configure AWS credentials (aws configure)")
        raise

def step_check_execution_role(session):
    """Step 1: Check for PartnerAiAppExecutionRole"""
    print("\n=== Step 1: Check IAM Execution Role ===\n")
    
    role_name = "PartnerAiAppExecutionRole"
    print(f"Checking for role: {role_name}...")
    
    exists, role_data = check_iam_role(role_name, session)
    
    if exists:
        print(f"✓ Role '{role_name}' exists")
        print(f"  ARN: {role_data['Arn']}")
        role_arn = role_data['Arn']
    else:
        print(f"✗ Role '{role_name}' does not exist")
        create = inquirer.confirm(
            message="Would you like to create it?",
            default=True
        ).execute()
        
        if create:
            role = create_execution_role(role_name, session)
            role_arn = role['Arn']
        else:
            raise Exception("Execution role is required to continue")
    
    return role_arn

def step_deploy_partner_app(region, execution_role_arn, account_id, session):
    """Step 2: Deploy Partner AI App"""
    print("\n=== Step 2: Deploy Partner AI App ===\n")
    
    app_configs = {
        'comet': {
            'type': 'comet',
            'tiers': ['comet.small', 'comet.medium', 'comet.large']
        },
        'fiddler': {
            'type': 'fiddler',
            'tiers': ['fiddler.small', 'fiddler.medium', 'fiddler.large']
        },
        'deepchecks': {
            'type': 'deepchecks-llm-evaluation',
            'tiers': ['deepchecks-llm-evaluation.small', 'deepchecks-llm-evaluation.medium', 'deepchecks-llm-evaluation.large']
        }
    }
    
    app_name = inquirer.select(
        message="Select Partner AI App:",
        choices=['comet', 'fiddler', 'deepchecks']
    ).execute()
    
    app_config = app_configs[app_name]
    
    tier = inquirer.select(
        message=f"Select tier for {app_name}:",
        choices=app_config['tiers']
    ).execute()
    
    print(f"\nDeploying {app_name} ({tier})...")
    
    sagemaker = session.client('sagemaker', region_name=region)
    
    params = {
        'Name': app_name,
        'Type': app_config['type'],
        'AuthType': 'IAM',
        'ExecutionRoleArn': execution_role_arn,
        'Tier': tier,
        'EnableIamSessionBasedIdentity': True,
        'Tags': [
            {'Key': 'Application', 'Value': app_name},
            {'Key': 'ManagedBy', 'Value': 'DeploymentScript'}
        ]
    }
    
    if app_name == 'comet':
        params['ApplicationConfig'] = {
            'AdminUsers': ['default']
        }
    elif app_name == 'fiddler':
        params['ApplicationConfig'] = {
            'AdminUsers': ['default']
        }
    elif app_name == 'deepchecks':
        params['ApplicationConfig'] = {
            'Arguments': {
                'modelIds_comma_delimited': 'anthropic.claude-3-sonnet-20240229-v1:0,anthropic.claude-3-haiku-20240307-v1:0',
                'inferenceProfiles_comma_delimited': 'us.anthropic.claude-3-5-sonnet-20241022-v2:0,us.anthropic.claude-3-5-haiku-20241022-v1:0'
            }
        }
    
    try:
        response = sagemaker.create_partner_app(**params)
        print(f"✓ Partner app '{app_name}' deployed successfully")
        print(f"  ARN: {response['Arn']}")
        return response
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUse':
            print(f"✗ Partner app '{app_name}' already exists")
        else:
            raise

def list_sagemaker_domains(region, session):
    """List all SageMaker domains in region"""
    sagemaker = session.client('sagemaker', region_name=region)
    try:
        response = sagemaker.list_domains()
        return response.get('Domains', [])
    except Exception as e:
        print(f"Error listing domains: {e}")
        return []

def get_domain_execution_role(domain_id, region, session):
    """Get default execution role for a domain"""
    sagemaker = session.client('sagemaker', region_name=region)
    try:
        response = sagemaker.describe_domain(DomainId=domain_id)
        return response['DefaultUserSettings']['ExecutionRole']
    except Exception as e:
        print(f"Error getting domain execution role: {e}")
        return None

def check_trust_policy_exists(role_name, session):
    """Check if SageMaker trust policy already exists with both required actions"""
    iam = session.client('iam')
    try:
        response = iam.get_role(RoleName=role_name)
        trust_policy = response['Role']['AssumeRolePolicyDocument']
        
        for statement in trust_policy.get('Statement', []):
            principal = statement.get('Principal', {})
            service = principal.get('Service', [])
            if isinstance(service, str):
                service = [service]
            if 'sagemaker.amazonaws.com' in service:
                actions = statement.get('Action', [])
                if isinstance(actions, str):
                    actions = [actions]
                # Check if BOTH sts:AssumeRole AND sts:TagSession are present
                if 'sts:AssumeRole' in actions and 'sts:TagSession' in actions:
                    return True
        return False
    except Exception as e:
        print(f"Error checking trust policy: {e}")
        return False

def append_trust_policy(role_name, session):
    """Append SageMaker trust policy to role"""
    iam = session.client('iam')
    try:
        response = iam.get_role(RoleName=role_name)
        trust_policy = response['Role']['AssumeRolePolicyDocument']
        
        # Find existing SageMaker statement and update it, or add new one
        sagemaker_statement_found = False
        for statement in trust_policy['Statement']:
            principal = statement.get('Principal', {})
            service = principal.get('Service', [])
            if isinstance(service, str):
                service = [service]
            
            if 'sagemaker.amazonaws.com' in service:
                # Update existing SageMaker statement with both actions
                statement['Action'] = ['sts:AssumeRole', 'sts:TagSession']
                sagemaker_statement_found = True
                break
        
        # If no SageMaker statement exists, add a new one
        if not sagemaker_statement_found:
            new_statement = {
                "Effect": "Allow",
                "Principal": {"Service": ["sagemaker.amazonaws.com"]},
                "Action": ["sts:AssumeRole", "sts:TagSession"]
            }
            trust_policy['Statement'].append(new_statement)
        
        iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(trust_policy)
        )
        print(f"✓ Updated SageMaker trust policy for role '{role_name}'")
    except Exception as e:
        print(f"✗ Error updating trust policy: {e}")
        raise

def check_managed_policy_attached(role_name, policy_arn, session):
    """Check if managed policy is already attached"""
    iam = session.client('iam')
    try:
        response = iam.list_attached_role_policies(RoleName=role_name)
        for policy in response.get('AttachedPolicies', []):
            if policy['PolicyArn'] == policy_arn:
                return True
        return False
    except Exception as e:
        print(f"Error checking managed policy: {e}")
        return False

def attach_managed_policy(role_name, policy_arn, session):
    """Attach managed policy to role"""
    iam = session.client('iam')
    try:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
        print(f"✓ Attached managed policy to role '{role_name}'")
    except Exception as e:
        print(f"✗ Error attaching managed policy: {e}")
        raise

def check_inline_policy_exists(role_name, policy_name, session):
    """Check if inline policy exists"""
    iam = session.client('iam')
    try:
        response = iam.list_role_policies(RoleName=role_name)
        return policy_name in response.get('PolicyNames', [])
    except Exception as e:
        print(f"Error checking inline policy: {e}")
        return False

def put_inline_policy(role_name, policy_name, session):
    """Put inline policy on role"""
    iam = session.client('iam')
    
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "sagemaker:DescribePartnerApp",
                "sagemaker:ListPartnerApps",
                "sagemaker:CreatePartnerAppPresignedUrl",
                "sagemaker:CallPartnerAppApi"
            ],
            "Resource": "arn:aws:sagemaker:*:*:partner-app/app-*"
        }]
    }
    
    try:
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document)
        )
        print(f"✓ Added inline policy '{policy_name}' to role '{role_name}'")
    except Exception as e:
        print(f"✗ Error adding inline policy: {e}")
        raise

def create_sagemaker_domain(region, session):
    """Create SageMaker domain using CloudFormation"""
    cfn = session.client('cloudformation', region_name=region)
    
    timestamp = datetime.now().strftime('%Y%m%d%H%M')
    stack_name = f"sagemaker-domain-{timestamp}"
    
    template_body = """AWSTemplateFormatVersion: '2010-09-09'
Description: 'SageMaker Domain with Quick Setup configuration'

Resources:
  SageMakerExecutionRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal:
              Service:
                - sagemaker.amazonaws.com
            Action:
              - sts:AssumeRole
              - sts:TagSession
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/AmazonSageMakerFullAccess
      Path: /service-role/

  PartnerAppUserPolicy:
    Type: AWS::IAM::Policy
    Properties:
      PolicyName: sagemaker-partner-app-user-policy
      PolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Action:
              - sagemaker:DescribePartnerApp
              - sagemaker:ListPartnerApps
              - sagemaker:CreatePartnerAppPresignedUrl
              - sagemaker:CallPartnerAppApi
            Resource: !Sub 'arn:aws:sagemaker:${AWS::Region}:${AWS::AccountId}:partner-app/app-*'
      Roles:
        - !Ref SageMakerExecutionRole

  SageMakerDomain:
    Type: AWS::SageMaker::Domain
    Properties:
      AuthMode: IAM
      DefaultUserSettings:
        ExecutionRole: !GetAtt SageMakerExecutionRole.Arn
      DomainName: !Sub 'sagemaker-domain-${AWS::StackName}'
      SubnetIds:
        - !Ref Subnet
      VpcId: !Ref VPC

  DefaultUserProfile:
    Type: AWS::SageMaker::UserProfile
    Properties:
      DomainId: !Ref SageMakerDomain
      UserProfileName: default-user

  VPC:
    Type: AWS::EC2::VPC
    Properties:
      CidrBlock: 10.0.0.0/16
      EnableDnsHostnames: true
      EnableDnsSupport: true

  Subnet:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VPC
      CidrBlock: 10.0.1.0/24
      MapPublicIpOnLaunch: true

  InternetGateway:
    Type: AWS::EC2::InternetGateway

  AttachGateway:
    Type: AWS::EC2::VPCGatewayAttachment
    Properties:
      VpcId: !Ref VPC
      InternetGatewayId: !Ref InternetGateway

  RouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref VPC

  Route:
    Type: AWS::EC2::Route
    DependsOn: AttachGateway
    Properties:
      RouteTableId: !Ref RouteTable
      DestinationCidrBlock: 0.0.0.0/0
      GatewayId: !Ref InternetGateway

  SubnetRouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref Subnet
      RouteTableId: !Ref RouteTable

Outputs:
  DomainId:
    Value: !Ref SageMakerDomain
    Description: SageMaker Domain ID
  DomainArn:
    Value: !GetAtt SageMakerDomain.DomainArn
    Description: SageMaker Domain ARN
  UserProfileName:
    Value: default-user
    Description: Default User Profile Name
"""
    
    print(f"\nCreating CloudFormation stack '{stack_name}'...")
    
    try:
        cfn.create_stack(
            StackName=stack_name,
            TemplateBody=template_body,
            Capabilities=['CAPABILITY_NAMED_IAM']
        )
        
        print("Waiting for stack creation to complete (this may take several minutes)...")
        waiter = cfn.get_waiter('stack_create_complete')
        waiter.wait(StackName=stack_name)
        
        response = cfn.describe_stacks(StackName=stack_name)
        outputs = response['Stacks'][0].get('Outputs', [])
        
        domain_id = None
        for output in outputs:
            if output['OutputKey'] == 'DomainId':
                domain_id = output['OutputValue']
                break
        
        print(f"✓ SageMaker domain created successfully")
        print(f"  Stack: {stack_name}")
        print(f"  Domain ID: {domain_id}")
        
        return domain_id
    except Exception as e:
        print(f"✗ Error creating domain: {e}")
        raise

def step_configure_domain_role(region, session):
    """Step 3: Configure SageMaker Domain Execution Role"""
    print("\n=== Step 3: Configure SageMaker Domain Execution Role ===\n")
    
    print("Listing SageMaker domains...")
    domains = list_sagemaker_domains(region, session)
    
    if not domains:
        print("✗ No SageMaker domains found in this region")
        create = inquirer.confirm(
            message="Would you like to create a new SageMaker domain?",
            default=True
        ).execute()
        
        if create:
            domain_id = create_sagemaker_domain(region, session)
            domains = list_sagemaker_domains(region, session)
        else:
            print("Skipping domain configuration")
            return
    
    print(f"\nFound {len(domains)} domain(s):")
    for domain in domains:
        print(f"  - {domain['DomainName']} (ID: {domain['DomainId']})")
    
    # Get execution roles from all domains
    domain_roles = {}
    for domain in domains:
        role_arn = get_domain_execution_role(domain['DomainId'], region, session)
        if role_arn:
            role_name = role_arn.split('/')[-1]
            domain_roles[f"{domain['DomainName']} - {role_name}"] = role_name
    
    if not domain_roles:
        print("✗ No execution roles found")
        return
    
    selected = inquirer.select(
        message="Select execution role to configure:",
        choices=list(domain_roles.keys())
    ).execute()
    
    role_name = domain_roles[selected]
    print(f"\nConfiguring role: {role_name}")
    
    # Check and append trust policy
    if check_trust_policy_exists(role_name, session):
        print("✓ SageMaker trust policy already exists")
    else:
        append_trust_policy(role_name, session)
    
    # Check and attach managed policy
    managed_policy_arn = "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess"
    if check_managed_policy_attached(role_name, managed_policy_arn, session):
        print("✓ AmazonSageMakerFullAccess already attached")
    else:
        attach_managed_policy(role_name, managed_policy_arn, session)
    
    # Check and add inline policy
    inline_policy_name = "sagemaker-partner-app-user-policy"
    if check_inline_policy_exists(role_name, inline_policy_name, session):
        print("✓ Partner app user policy already exists")
    else:
        put_inline_policy(role_name, inline_policy_name, session)

def main():
    print("\n" + "="*60)
    print("  Partner AI App Deployment Tool")
    print("="*60)
    
    try:
        profile = select_aws_profile()
        identity, session = validate_credentials(profile)
        account_id = identity['Account']
        
        region = inquirer.text(
            message="\nEnter AWS region:",
            default="us-east-1"
        ).execute()
        
        # Ask which step to start from
        start_step = inquirer.select(
            message="Select starting step:",
            choices=[
                Choice(value=1, name="Step 1: Check/Create IAM Execution Role"),
                Choice(value=2, name="Step 2: Deploy Partner AI App"),
                Choice(value=3, name="Step 3: Configure SageMaker Domain Role")
            ],
            default=1
        ).execute()
        
        if start_step <= 1:
            execution_role_arn = step_check_execution_role(session)
            print("\n✓ Step 1 complete!")
        else:
            execution_role_arn = None
        
        if start_step <= 2 and execution_role_arn:
            step_deploy_partner_app(region, execution_role_arn, account_id, session)
            print("\n✓ Step 2 complete!")
        
        if start_step <= 3:
            step_configure_domain_role(region, session)
            print("\n✓ Step 3 complete!")
        
        print("\n" + "="*60)
        print("  Deployment Complete!")
        print("="*60)
        
    except KeyboardInterrupt:
        print("\n\nDeployment cancelled by user.")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        raise

if __name__ == "__main__":
    main()
