.PHONY: validate test demo api

validate:
	python -m jvto_agent_runtime validate-repo

test:
	pytest

demo:
	python -m jvto_agent_runtime build-release --knowledge-root tests/fixtures/knowledge_catalog --core-root tests/fixtures/itinerary_core --release-id fixture-release
	python -m jvto_agent_runtime validate-release --release-dir dist/releases/fixture-release
	python -m jvto_agent_runtime decide --release-dir dist/releases/fixture-release --intent plan_itinerary --query "Surabaya to Bali via Tumpak Sewu Bromo and Ijen" --entities '{"pickup_location":"Surabaya","dropoff_location":"Bali","requested_destinations":["Tumpak Sewu","Bromo","Ijen"],"travel_date":"2026-08-10","number_of_guests":4,"pickup_time":"08:00","duration_days":4}'

api:
	uvicorn jvto_agent_runtime.api:app --reload --port 8080
