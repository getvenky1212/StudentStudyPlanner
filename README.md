# Student Study Planner

A Streamlit dashboard for tracking Canvas coursework and preparing for upcoming
assignments and tests. Without Canvas credentials, it starts in demo mode with
sample courses and materials.

## Features

- View assignments and tests with due-date and overdue indicators.
- Browse course materials, including syllabus content, Canvas pages, and files.
- Generate AI study guides from course materials with Gemini on Google Cloud.
- Review summaries, key concepts, practice questions, flashcards, and a study plan.
- Save planner data locally between sessions.

## Setup

Requires Python 3.10 or newer. From the project directory, create and activate a
virtual environment, install dependencies, and start the app:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run study_hub.py
```

On Windows, activate the environment with `.venv\\Scripts\\activate` instead.

## Canvas

Canvas is optional. In the app sidebar, enter your school's Canvas address and
an access token created in Canvas under **Account > Settings**. You can also
provide them as environment variables:

```sh
export CANVAS_BASE_URL="https://yourschool.instructure.com"
export CANVAS_TOKEN="your-canvas-access-token"
```

Without both values, the app uses its sample courses. The Canvas integration
reads course data and materials; it does not submit assignments or modify
Canvas content.

## Gemini Study Guides

To enable AI-generated study guides:

1. Select a Google Cloud project and enable the Vertex AI API.
2. Authenticate locally with Application Default Credentials:

	```sh
	gcloud auth application-default login
	```

3. Set the project ID before starting Streamlit:

	```sh
	export GOOGLE_CLOUD_PROJECT="your-google-cloud-project-id"
	```

Optionally set `GOOGLE_CLOUD_LOCATION` (default `us-central1`) and
`GEMINI_MODEL` (default `gemini-3.5-flash`). These environment variables
override the defaults in [ai_study_settings.py](ai_study_settings.py), where
the model, location, attachment limits, and generation limits can be adjusted.

When AI is enabled, course material selected for a guide is sent to Gemini to
generate the study content. Avoid sending material that you are not permitted
to share with that service.

## Local Data

The app stores saved guides, completed tasks, and planner data in
`study_hub_data.json` beside the application. This file is local and excluded
from Git; deleting it resets saved planner data.

## Project Files

- `study_hub.py` - Streamlit dashboard and local planner
- `canvas_client.py` - read-only Canvas API integration
- `ai_study.py` - Gemini study-guide generation
- `ai_study_config.py` - study-guide schema and system prompt
- `ai_study_settings.py` - Gemini defaults and generation limits