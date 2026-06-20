"""
ComfyUI Server Interface Module
Provides a Python interface to ComfyUI server for workflow execution.
"""

import sys
import json
import time
import uuid
from pathlib import Path
import requests


class JobCancelled(Exception):
    """Raised when a job is cancelled while polling for completion."""


class ComfyServer:
    """Main interface to ComfyUI server."""

    def __init__(self, server="192.168.1.135:8000"):
        """
        Initialize ComfyUI server connection.

        Args:
            server: Server address in format "host:port"
        """
        self.server = server
        self.client_id = str(uuid.uuid4())

    def load_workflow(self, workflow_path):
        """
        Load and parse workflow JSON file.

        Args:
            workflow_path: Path to workflow JSON file

        Returns:
            dict: Parsed workflow data

        Raises:
            FileNotFoundError: If workflow file doesn't exist
            json.JSONDecodeError: If workflow file contains invalid JSON
        """
        try:
            with open(workflow_path, 'r') as f:
                workflow = json.load(f)
            return workflow
        except FileNotFoundError:
            raise FileNotFoundError(f"Workflow file not found: {workflow_path}")
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(f"Invalid JSON in workflow file: {e}", e.doc, e.pos)

    def convert_ui_to_api_format(self, workflow):
        """
        Convert ComfyUI UI format workflow to API format.

        Args:
            workflow: Workflow dict in UI or API format

        Returns:
            dict: Workflow in API format
        """
        # Check if already in API format (flat dict with node IDs as keys)
        # or UI format (has 'nodes' array)
        if "nodes" not in workflow:
            # Already in API format
            return workflow

        api_workflow = {}

        # Build link lookup table
        link_map = {}
        if "links" in workflow:
            for link in workflow["links"]:
                # Link format: [link_id, source_node_id, source_slot, target_node_id, target_slot, type]
                link_id = link[0]
                source_node_id = str(link[1])
                source_slot = link[2]
                link_map[link_id] = [source_node_id, source_slot]

        # Convert each node
        for node in workflow["nodes"]:
            node_id = str(node["id"])

            # Skip certain node types that are UI-only
            node_type = node.get("type", "")
            if node_type in ["PrimitiveNode"]:
                # These are special UI nodes that feed values to other nodes
                continue

            # Build the API node structure
            api_node = {
                "class_type": node_type,
                "inputs": {}
            }

            # Get widget values that aren't connected to inputs
            widget_values = node.get("widgets_values", [])
            widget_idx = 0

            # Process inputs
            if "inputs" in node:
                for inp in node["inputs"]:
                    input_name = inp["name"]

                    # Check if this input is connected via a link
                    if "link" in inp and inp["link"] is not None:
                        link_id = inp["link"]
                        if link_id in link_map:
                            api_node["inputs"][input_name] = link_map[link_id]
                    # Otherwise, check if it has a widget value
                    elif "widget" in inp:
                        # This input has a widget, use widget_values
                        if widget_idx < len(widget_values):
                            api_node["inputs"][input_name] = widget_values[widget_idx]
                            widget_idx += 1

            api_workflow[node_id] = api_node

        return api_workflow

    def submit_workflow(self, workflow):
        """
        Submit workflow to ComfyUI server.

        Args:
            workflow: Workflow dict in API format

        Returns:
            str: Prompt ID for tracking execution

        Raises:
            requests.exceptions.RequestException: On connection error
            RuntimeError: On server error response
        """
        url = f"http://{self.server}/prompt"
        payload = {
            "prompt": workflow,
            "client_id": self.client_id
        }

        try:
            response = requests.post(url, json=payload, timeout=30)

            # Check for error response
            if response.status_code != 200:
                try:
                    error_data = response.json()
                    raise RuntimeError(
                        f"Error submitting workflow: {response.status_code}\n"
                        f"Server response: {json.dumps(error_data, indent=2)}"
                    )
                except json.JSONDecodeError:
                    raise RuntimeError(
                        f"Error submitting workflow: {response.status_code}\n"
                        f"Server response: {response.text}"
                    )

            result = response.json()

            if "prompt_id" in result:
                return result["prompt_id"]
            else:
                raise RuntimeError(f"Unexpected response format: {result}")

        except requests.exceptions.RequestException as e:
            raise requests.exceptions.RequestException(f"Error connecting to server: {e}")

    def poll_status(self, prompt_id, timeout=600, callback=None, cancel_event=None):
        """
        Poll workflow execution status until completion or timeout.

        Args:
            prompt_id: Prompt ID returned from submit_workflow
            timeout: Maximum wait time in seconds
            callback: Optional callback function for status updates
            cancel_event: Optional threading.Event; when set, polling aborts

        Returns:
            dict: Completed prompt data with outputs

        Raises:
            TimeoutError: If execution exceeds timeout
            RuntimeError: If execution fails
            JobCancelled: If cancel_event is set during polling
        """
        url = f"http://{self.server}/history/{prompt_id}"
        start_time = time.time()
        last_status = None

        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled()

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"Timeout after {timeout} seconds")

            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                history = response.json()

                # Check if prompt_id exists in history
                if prompt_id in history:
                    prompt_data = history[prompt_id]

                    # Check for completion
                    if "outputs" in prompt_data:
                        return prompt_data

                    # Check for errors
                    if "status" in prompt_data:
                        status = prompt_data["status"]
                        if "status_str" in status:
                            status_str = status["status_str"]
                            if status_str != last_status:
                                if callback:
                                    callback(f"Status: {status_str}")
                                last_status = status_str

                            if status_str == "error":
                                error_msg = status.get("messages", ["Unknown error"])
                                raise RuntimeError(f"Execution error: {error_msg}")

                # Wait before next poll
                time.sleep(2)
                if callback:
                    callback(".")

            except requests.exceptions.RequestException as e:
                if callback:
                    callback(f"\nPolling error: {e}")
                time.sleep(2)

    # Output node result keys we know how to download. ComfyUI puts still images
    # under "images"; video/animation nodes (e.g. VHS_VideoCombine) put their
    # results under "gifs", and some nodes use "videos". They all share the same
    # {filename, subfolder, type} shape, so we treat them uniformly.
    OUTPUT_RESULT_KEYS = ("images", "gifs", "videos")

    def get_output_images(self, prompt_data):
        """
        Extract output media (images or videos) from completed prompt data.

        Args:
            prompt_data: Completed prompt data from poll_status

        Returns:
            list: List of dicts with media info (filename, subfolder, type)
        """
        images = []

        if "outputs" not in prompt_data:
            return images

        outputs = prompt_data["outputs"]

        # Iterate through all output nodes, collecting every known result kind.
        for node_output in outputs.values():
            for key in self.OUTPUT_RESULT_KEYS:
                for item in node_output.get(key, []):
                    filename = item["filename"]
                    item_type = item.get("type", "output")

                    # Skip temporary/preview results
                    if "_temp_" in filename or item_type == "temp":
                        continue

                    images.append({
                        "filename": filename,
                        "subfolder": item.get("subfolder", ""),
                        "type": item_type
                    })

        return images

    def download_image(self, filename, subfolder, img_type, output_path):
        """
        Download a single image from ComfyUI server.

        Args:
            filename: Image filename
            subfolder: Image subfolder on server
            img_type: Image type (output, temp, etc.)
            output_path: Directory path to save image

        Returns:
            Path: Path to downloaded file, or None on error
        """
        params = {
            "filename": filename,
            "subfolder": subfolder,
            "type": img_type
        }

        url = f"http://{self.server}/view"

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()

            # Save image
            output_file = Path(output_path) / filename
            with open(output_file, 'wb') as f:
                f.write(response.content)

            return output_file
        except Exception as e:
            print(f"Error downloading {filename}: {e}", file=sys.stderr)
            return None

    def upload_image(self, image_path, subfolder="", overwrite=True):
        """
        Upload an image to the ComfyUI server's input folder.

        Used to feed a previously generated image into a workflow (e.g. via a
        LoadImage node whose `image` input is a <INPUT_IMAGE> placeholder).

        Args:
            image_path: Path to the local image file to upload
            subfolder: Optional subfolder within the server's input folder
            overwrite: Overwrite an existing file with the same name

        Returns:
            str: The name to reference the image by in a LoadImage node
                 (prefixed with the subfolder if the server stored it in one)

        Raises:
            requests.exceptions.RequestException: On connection/HTTP error
        """
        image_path = Path(image_path)
        url = f"http://{self.server}/upload/image"
        data = {"overwrite": "true" if overwrite else "false"}
        if subfolder:
            data["subfolder"] = subfolder
        try:
            with open(image_path, "rb") as f:
                files = {"image": (image_path.name, f, "image/png")}
                response = requests.post(url, files=files, data=data, timeout=60)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise requests.exceptions.RequestException(f"Error uploading image: {e}")

        result = response.json()
        name = result.get("name", image_path.name)
        sub = result.get("subfolder", "")
        return f"{sub}/{name}" if sub else name

    def free_memory(self, unload_models=True, free_memory=True):
        """
        Ask ComfyUI to release GPU memory.

        Args:
            unload_models: Unload loaded models from GPU/CPU memory
            free_memory: Run garbage collection / free cached allocations

        Raises:
            requests.exceptions.RequestException: On connection error
            RuntimeError: On server error response
        """
        url = f"http://{self.server}/free"
        payload = {"unload_models": unload_models, "free_memory": free_memory}
        try:
            response = requests.post(url, json=payload, timeout=15)
            if response.status_code not in (200, 204):
                raise RuntimeError(f"Server returned {response.status_code}: {response.text}")
        except requests.exceptions.RequestException as e:
            raise requests.exceptions.RequestException(f"Error connecting to server: {e}")

    def interrupt(self, prompt_id=None):
        """
        Ask ComfyUI to stop the running prompt (and drop it from the queue if
        it hasn't started yet).

        Args:
            prompt_id: Optional prompt ID to also remove from the pending queue

        Raises:
            requests.exceptions.RequestException: On connection error
        """
        try:
            requests.post(f"http://{self.server}/interrupt", json={}, timeout=15)
            if prompt_id:
                requests.post(
                    f"http://{self.server}/queue",
                    json={"delete": [prompt_id]},
                    timeout=15,
                )
        except requests.exceptions.RequestException as e:
            raise requests.exceptions.RequestException(f"Error connecting to server: {e}")

    def download_images(self, images, output_path):
        """
        Download multiple images from server.

        Args:
            images: List of image dicts from get_output_images
            output_path: Directory path to save images

        Returns:
            list: List of successfully downloaded file paths
        """
        output_dir = Path(output_path)
        output_dir.mkdir(parents=True, exist_ok=True)

        downloaded_files = []
        for img in images:
            result = self.download_image(
                img["filename"],
                img["subfolder"],
                img["type"],
                output_dir
            )
            if result:
                downloaded_files.append(result)

        return downloaded_files
