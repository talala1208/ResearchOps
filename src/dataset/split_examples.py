from langsmith import Client
from dotenv import load_dotenv
load_dotenv()

client = Client()

dataset_id = "95c8685d-805e-405b-9e89-01ce30d2f601"

examples = list(client.list_examples(dataset_id=dataset_id))

client.update_examples(
    example_ids=[example.id for example in examples],
    splits=[
        (example.metadata or {}).get("scene", "standard")
        for example in examples
    ],
)
