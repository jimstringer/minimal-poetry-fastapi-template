# Development commands for the minimal-poetry-fastapi project
project_name := minimal_poetry_fastapi-template
# The package name is used in the `run` command to specify the module to run with uvicorn.
# It should match the name of the package in your `pyproject.toml` file.
package_name := app

.PHONY: help setup run test css css-watch

# Keep this rule first so that it is the default target when running `make` without arguments.
help:
	@echo "Available commands:"
	@echo "  make setup     - Install dependencies"
	@echo "  make run       - Run the application and watch for changes"
	@echo "  make test      - Run tests"
	@echo "  make css       - Compile Tailwind CSS"
	@echo "  make css-watch - Watch for changes in Tailwind CSS and recompile automatically"
	@echo "  make help      - Show this help message"

setup:
	@echo "Setting up the project..."
	# Install Python dependencies
	@poetry install
	# Install Node.js dependencies for Tailwind CSS
	@cd tailwind && npm install
	@echo "=== Setup complete ==="

css:
	@echo "Compiling CSS..."
	# we need to run this in the tailwind directory because of the way the tailwind config is set up
	@cd tailwind && npm run css

css-watch:
	@echo "Watching for changes in Tailwind CSS..."
	@cd tailwind && npm run css-watch

run:
	@echo "Running the application..."
	@poetry run uvicorn $(package_name).main:app --reload

test:
	@echo "Running tests..."
	@poetry run pytest



