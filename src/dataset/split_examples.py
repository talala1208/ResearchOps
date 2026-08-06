from langsmith import Client

client = Client()

dataset_id = "真实的-dataset-id"

examples = list(client.list_examples(dataset_id=dataset_id))

client.update_examples(
    example_ids=[example.id for example in examples],
    splits=[
        (example.metadata or {}).get("scene", "standard")
        for example in examples
    ],
)
