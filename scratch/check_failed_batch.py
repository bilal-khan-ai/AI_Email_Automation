from openai import OpenAI
from config import Config
import json

def main():
    client = OpenAI(api_key=Config.OPENAI_API_KEY)
    
    # Retrieve details for Batch 2
    job_id = "batch_6a0d9d877e2c8190999dfff30325f868"
    print(f"Retrieving details for Batch 2 (Job: {job_id})...")
    
    try:
        job = client.batches.retrieve(job_id)
        # Convert job object to dictionary representation
        details = {
            "id": job.id,
            "endpoint": job.endpoint,
            "errors": getattr(job, "errors", None),
            "error_file_id": getattr(job, "error_file_id", None),
            "status": job.status,
            "input_file_id": job.input_file_id,
            "output_file_id": job.output_file_id
        }
        
        # If there's an error object, serialize it
        if details["errors"]:
            # job.errors is a BatchErrors object
            error_data = []
            for err in details["errors"].data:
                error_data.append({
                    "code": getattr(err, "code", None),
                    "message": getattr(err, "message", None),
                    "param": getattr(err, "param", None),
                    "line": getattr(err, "line", None)
                })
            details["errors"] = error_data
            
        print(json.dumps(details, indent=2))
        
        # If there's an error file, we can retrieve its content
        if details["error_file_id"]:
            print(f"\nRetrieving error file content (File: {details['error_file_id']})...")
            err_file = client.files.content(details["error_file_id"])
            print(err_file.text)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
