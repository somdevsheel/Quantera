
import boto3
from botocore.exceptions import ClientError

BUCKET = "quant-global-data-lake-prod"
REGION = "ap-southeast-2"

def test_s3():
    s3 = boto3.client("s3", region_name=REGION)

    try:
        response = s3.head_bucket(Bucket=BUCKET)
        print("S3 connection successful!")

        test_content = b"Quantera Python to S3 connection successful."

        s3.put_object(
            Bucket=BUCKET,
            Key="bronze/test/python_connection.txt",
            Body=test_content
        )
        print("Test file uploaded successfully!")

        result = s3.get_object(
            Bucket=BUCKET,
            Key="bronze/test/python_connection.txt"
        )

        downloaded_content = result["Body"].read()
        print("Downloaded content:", downloaded_content.decode())

        print("\nAll S3 tests passed!")

    except ClientError as error:
        print("AWS error:", error)

if __name__ == "__main__":
    test_s3()
