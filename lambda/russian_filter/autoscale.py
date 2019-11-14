import json
import logging
import boto3
import sys
from re import search

logger = logging.getLogger()
logger.setLevel(logging.INFO)

autoscaling = boto3.client('autoscaling')
ec2 = boto3.client('ec2')



LIFECYCLE_KEY = "LifecycleHookName"
ASG_KEY = "AutoScalingGroupName"

# Fetches public IP of an instance via EC2 API
def fetch_public_ip_from_ec2(instance_id):
    logger.info("Fetching private IP for instance-id: %s", instance_id)

    ec2_response = ec2.describe_instances(InstanceIds=[instance_id])
    ip_address = ec2_response['Reservations'][0]['Instances'][0]['NetworkInterfaces'][0]['Association']['PublicIp']


    logger.info("Found private IP for instance-id %s: %s", instance_id, ip_address)

    return ip_address

def check_russian_filter_ip(ip_address):
    s3 = boto3.resource('s3')

    obj = s3.Object('russian-ips-exporter', 'clean-ips-list.txt')
    fh=obj.get()['Body'].read().decode('utf-8') 
    if search(ip_address,fh):
        logger.info("Found IP %s in Russian blacklist",ip_address)
        return True
    logger.info("IP %s is NOT in Russian blacklist",ip_address)
    return False


# Processes a scaling event
# Builds a hostname from tag metadata, fetches a private IP, and updates records accordingly
def process_message(message):
    logger.info("Processing %s event", message['LifecycleTransition'])

    if message['LifecycleTransition'] == "autoscaling:EC2_INSTANCE_LAUNCHING":
        instance_id =  message['EC2InstanceId']

        public_ip = fetch_public_ip_from_ec2(instance_id)
        
        return check_russian_filter_ip(public_ip)
    else:
        logger.error("Encountered unknown event type: %s", message['LifecycleTransition'])
        return False




# Picks out the message from a SNS message and deserializes it
def process_record(record):
    return process_message(json.loads(record['Sns']['Message']))

# Main handler where the SNS events end up to
# Events are bulked up, so process each Record individually
def lambda_handler(event, context):
    logger.info("Processing SNS event: " + json.dumps(event))
    for record in event['Records']:
        action='CONTINUE'
        if process_record(record):
            logger.info("Exiting because IP found in the blacklist")
            action = 'ABANDON'

        # Finish the asg lifecycle operation by sending a continue result
        logger.info("Finishing ASG action")
        message =json.loads(record['Sns']['Message'])
        if LIFECYCLE_KEY in message and ASG_KEY in message :
            response = autoscaling.complete_lifecycle_action (
                LifecycleHookName = message['LifecycleHookName'],
                AutoScalingGroupName = message['AutoScalingGroupName'],
                InstanceId = message['EC2InstanceId'],
                LifecycleActionToken = message['LifecycleActionToken'],
                LifecycleActionResult = action
            
            )
            logger.info("ASG action complete: %s", response)
        else :
            logger.error("No valid JSON message")

# if invoked manually, assume someone pipes in a event json
if __name__ == "__main__":
    logging.basicConfig()

    lambda_handler(json.load(sys.stdin), None)
