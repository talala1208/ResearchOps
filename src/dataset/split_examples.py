from langsmith import Client
from dotenv import load_dotenv
load_dotenv()

client = Client()

dataset_id = "your_langsmith_dataset_id"

examples = list(client.list_examples(dataset_id=dataset_id))

client.update_examples(
    example_ids=[example.id for example in examples],
    splits=[
        (example.metadata or {}).get("scene", "standard")
        for example in examples
    ],
)
