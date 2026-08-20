"""Allow ``python -m job_recommendation_api`` to run the uvicorn server."""

from job_recommendation_api.main import main

if __name__ == "__main__":
    main()
