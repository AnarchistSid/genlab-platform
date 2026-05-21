"""Crispy-based highlight detection scorer.

Used by the SCORE_CLIPS pipeline stage as the highlight_detection dimension
(weight 0.12) in GamingScoringStrategy.

Uses pre-trained neural network models from the Crispy project
(https://github.com/Flowtter/crispy, MIT License) to detect
kills/highlights in gaming clip frames.

Supported games: Valorant, CS2, Overwatch 2.
Unsupported games receive a neutral fallback score.

Models are custom numpy MLPs with sigmoid activation, stored as .npy files.
Preprocessing replicates Crispy's game-specific frame transforms:
  - Valorant: grayscale -> edge detection + mask -> crop left/right halves
  - CS2: edge detection + enhance -> grayscale -> mask overlay
  - Overwatch: color filter (red->white) -> grayscale (numpy vectorized)

Model weights are stored as .npz files (one named array per layer) so they
load safely with allow_pickle=False. Original .npy files used numpy object
arrays which required allow_pickle=True (an RCE vector).
"""

import logging
import os

import cv2
import numpy as np
import scipy.special
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)


class CrispyScorer:
    """Scores video clips for game-specific highlight/kill detection.

    Uses Crispy's pre-trained numpy MLP models to analyze kill feed regions
    in video frames. Each frame is cropped, preprocessed, and fed through
    a forward pass. Consecutive positive detections are grouped and
    false positives (isolated frames) are filtered out.

    The final score is a normalized ratio of highlight frames to total frames.
    """

    def __init__(self, config: dict, project_root: str) -> None:
        self.config = config
        self.project_root = project_root
        self._models: dict = {}  # game_key -> loaded numpy weights
        self._masks: dict = {}  # game_key -> PIL mask image (or None)
        self._game_name_map: dict = {}  # lowercased game name -> game_key
        self._build_game_name_map()

    def _build_game_name_map(self) -> None:
        """Build lookup from display game name to internal model key."""
        for key, model_cfg in self.config.get("models", {}).items():
            for name in model_cfg.get("game_names", []):
                self._game_name_map[name.lower()] = key

    def is_enabled(self) -> bool:
        """Check if highlight detection is enabled in config."""
        return bool(self.config.get("enable_highlight_detection", False))

    def supports_game(self, game_title: str) -> bool:
        """Check if a model exists for the given game title."""
        return game_title.lower() in self._game_name_map

    # --- Model loading (lazy) ---

    def _load_model(self, game_key: str) -> list[np.ndarray]:
        """Load model weights from .npz file, with caching.

        Returns a list of weight matrices (one per MLP layer).
        """
        if game_key in self._models:
            return self._models[game_key]

        cfg = self.config["models"][game_key]
        raw_path = os.path.join(self.project_root, cfg["weights_path"])
        # Prefer .npz (safe), fall back to .npy path with extension swap
        if raw_path.endswith(".npy"):
            npz_path = raw_path[:-4] + ".npz"
        else:
            npz_path = raw_path

        if not os.path.exists(npz_path):
            raise FileNotFoundError(f"Model weights not found: {npz_path}")

        data = np.load(npz_path, allow_pickle=False)
        weights = [data[f"layer_{i}"] for i in range(len(data.files))]
        self._models[game_key] = weights
        logger.debug("Loaded Crispy model for %s from %s", game_key, npz_path)
        return weights

    def _load_mask(self, game_key: str) -> Image.Image | None:
        """Load preprocessing mask image, with caching."""
        if game_key in self._masks:
            return self._masks[game_key]

        cfg = self.config["models"][game_key]
        mask_path = cfg.get("mask_path")

        if not mask_path:
            self._masks[game_key] = None
            return None

        full_path = os.path.join(self.project_root, mask_path)
        if not os.path.exists(full_path):
            logger.warning("Mask file not found: %s", full_path)
            self._masks[game_key] = None
            return None

        mask = Image.open(full_path)
        self._masks[game_key] = mask
        return mask

    # --- Coordinate computation ---

    def _compute_crop_box(
        self,
        region_cfg: dict,
        stretch: bool = False,
    ) -> tuple[int, int, int, int]:
        """Compute pixel crop coordinates from Crispy Box parameters.

        Replicates Crispy's Box class: x = half - box_x + shift_x
        where half = 720 (stretch) or 960 (non-stretch).

        Returns (crop_x, crop_y, width, height).
        """
        box_x = region_cfg["box_x"]
        box_y = region_cfg["box_y"]
        width = region_cfg["box_width"]
        height = region_cfg["box_height"]
        shift_x = region_cfg.get("shift_x", 0)

        half = 720 if stretch else 960
        crop_x = half - box_x + shift_x

        return (crop_x, box_y, width, height)

    # --- Neural network forward pass ---

    def _forward_pass(
        self,
        inputs: np.ndarray,
        weights: list[np.ndarray],
    ) -> np.ndarray:
        """Run sigmoid MLP forward pass (replicates Crispy NeuralNetwork.query).

        Returns shape (2, 1): [non_kill_confidence, kill_confidence].
        """
        x = np.array(inputs, ndmin=2).T
        for w in weights:
            x = scipy.special.expit(np.dot(w, x))
        return x

    # --- Game-specific preprocessing ---

    def _preprocess_frame(
        self,
        frame: np.ndarray,
        game_key: str,
    ) -> np.ndarray | None:
        """Apply game-specific preprocessing to a BGR video frame.

        Crops the kill feed region, applies Crispy's preprocessing pipeline,
        then converts to a normalized 1D pixel array.

        Returns 1D numpy array or None if preprocessing type is unknown.
        """
        cfg = self.config["models"][game_key]
        crop_x, crop_y, crop_w, crop_h = self._compute_crop_box(
            cfg["kill_feed_region"],
            stretch=False,
        )

        # Convert BGR (cv2) -> RGB (PIL)
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame_rgb)

        # Crop kill feed region
        cropped = pil_img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))

        # Apply game-specific preprocessing
        preprocessing = cfg.get("preprocessing", "")
        mask = self._load_mask(game_key)

        if preprocessing == "valorant_edge_mask":
            processed = self._preprocess_valorant(cropped, mask)
        elif preprocessing == "csgo2_edge_mask":
            processed = self._preprocess_csgo2(cropped, mask)
        elif preprocessing == "overwatch_color_filter":
            processed = self._preprocess_overwatch(cropped)
        else:
            return None

        # Extract R channel -> 1D array -> normalize (Crispy convention)
        r_channel = processed.getchannel("R")
        pixels = (
            list(r_channel.getdata())
            if hasattr(r_channel, "getdata")
            else list(r_channel.tobytes())
        )
        arr = (np.asarray(pixels, dtype=float) / 255.0 * 0.99) + 0.01
        return arr

    def _preprocess_valorant(
        self,
        image: Image.Image,
        mask: Image.Image | None,
    ) -> Image.Image:
        """Replicate Crispy's Valorant preprocessing pipeline.

        Grayscale -> edge detection + enhancement with mask -> crop left/right
        halves -> combine edges + enhanced (flipped) -> 50x80 final.
        """
        image = ImageOps.grayscale(image)

        def apply_filter_and_ops(
            img: Image.Image,
            img_filter: ImageFilter.Filter,
        ) -> Image.Image:
            filtered = img.filter(img_filter)
            filtered = filtered.crop(
                (1, 1, filtered.width - 2, filtered.height - 2),
            )
            if mask is not None:
                filtered.paste(mask, (0, 0), mask)
            left = filtered.crop((0, 0, 25, 60))
            right = filtered.crop((95, 0, 120, 60))
            final = Image.new("RGB", (50, 60))
            final.paste(left, (0, 0))
            final.paste(right, (25, 0))
            final = final.crop((0, 20, 50, 60))
            return final

        edges = apply_filter_and_ops(image, ImageFilter.FIND_EDGES)
        enhanced = apply_filter_and_ops(image, ImageFilter.EDGE_ENHANCE_MORE)

        final = Image.new("RGB", (50, 80))
        final.paste(edges, (0, 0))
        enhanced = enhanced.transpose(Image.FLIP_TOP_BOTTOM)
        final.paste(enhanced, (0, 40))
        return final

    def _preprocess_csgo2(
        self,
        image: Image.Image,
        mask: Image.Image | None,
    ) -> Image.Image:
        """Replicate Crispy's CS2 preprocessing pipeline.

        Edge detection + enhancement -> grayscale -> mask overlay -> 100x100.
        """
        processed = ImageOps.grayscale(
            image.filter(ImageFilter.FIND_EDGES).filter(
                ImageFilter.EDGE_ENHANCE_MORE,
            ),
        )
        final = Image.new("RGB", (100, 100))
        final.paste(processed, (0, 0))
        if mask is not None:
            final.paste(mask, (0, 0), mask)
        return final

    def _preprocess_overwatch(self, image: Image.Image) -> Image.Image:
        """Replicate Crispy's Overwatch preprocessing pipeline.

        Color filter: bright red->white, semi-red->amplify, others->suppress.
        Then grayscale -> 100x100.

        Uses numpy vectorized operations instead of per-pixel Python loops
        for O(1) array ops vs the original O(n^2) getpixel/putpixel approach.
        """
        arr = np.array(image, dtype=np.int16)
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

        out = np.empty_like(arr)

        # Condition 1: bright red -> white
        bright_red = (r > 200) & (g < 100) & (b < 100)
        # Condition 2: semi-red -> amplify red, suppress green/blue
        semi_red = (~bright_red) & (r > 200) & (g < 180) & (b < 180)
        # Condition 3: everything else -> slight red boost, heavy suppress
        other = ~bright_red & ~semi_red

        out[bright_red] = [255, 255, 255]

        out[semi_red, 0] = np.minimum(255, (r[semi_red] * 1.5).astype(np.int16))
        out[semi_red, 1] = np.maximum(0, (g[semi_red] * 0.3).astype(np.int16))
        out[semi_red, 2] = np.maximum(0, (b[semi_red] * 0.3).astype(np.int16))

        out[other, 0] = np.minimum(255, (r[other] * 1.1).astype(np.int16))
        out[other, 1] = np.maximum(0, (g[other] * 0.05).astype(np.int16))
        out[other, 2] = np.maximum(0, (b[other] * 0.05).astype(np.int16))

        merged = Image.fromarray(out.astype(np.uint8), "RGB")
        im = ImageOps.grayscale(merged)
        final = Image.new("RGB", (100, 100))
        final.paste(im, (0, 0))
        return final

    # --- Main scoring entry point ---

    def score_video(self, video_path: str, game_title: str) -> float:
        """Score a video clip for highlight/kill detection.

        Args:
            video_path: Path to the video file.
            game_title: Game display name (e.g., "VALORANT", "Counter-Strike 2").

        Returns:
            Float 0.0-1.0 representing highlight density.
            Returns fallback_score for unsupported/disabled games or errors.
        """
        fallback = self.config.get("fallback_score", 0.5)

        if not self.is_enabled():
            return fallback

        if not self.supports_game(game_title):
            return fallback

        game_key = self._game_name_map[game_title.lower()]

        try:
            weights = self._load_model(game_key)
        except FileNotFoundError:
            logger.warning("Model not found for %s, returning fallback", game_key)
            return fallback

        cfg = self.config["models"][game_key]
        confidence = cfg.get("confidence_threshold", 0.90)
        sample_rate = self.config.get("frame_sample_rate", 8)
        min_consecutive = self.config.get("min_consecutive_frames", 3)
        target_w, target_h = cfg["input_resolution"]

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning("Cannot open video: %s", video_path)
            return fallback

        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            frame_interval = max(1, int(fps / sample_rate))

            positive_frames: list[int] = []
            frame_idx = 0
            sample_idx = 0

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_idx % frame_interval == 0:
                    # Resize to target resolution if needed
                    h, w = frame.shape[:2]
                    if w != target_w or h != target_h:
                        frame = cv2.resize(frame, (target_w, target_h))

                    arr = self._preprocess_frame(frame, game_key)
                    if arr is not None:
                        output = self._forward_pass(arr, weights)
                        if output[1] >= confidence:
                            positive_frames.append(sample_idx)

                    sample_idx += 1
                frame_idx += 1
        finally:
            cap.release()

        if not positive_frames or sample_idx == 0:
            return 0.0

        # Group consecutive positive frames
        groups: list[list[int]] = []
        current_group = [positive_frames[0]]
        for i in range(1, len(positive_frames)):
            if positive_frames[i] - positive_frames[i - 1] == 1:
                current_group.append(positive_frames[i])
            else:
                groups.append(current_group)
                current_group = [positive_frames[i]]
        groups.append(current_group)

        # Filter groups shorter than min_consecutive (false positives)
        valid_groups = [g for g in groups if len(g) >= min_consecutive]

        if not valid_groups:
            return 0.0

        # Score: normalized ratio of highlight frames to total sampled frames
        highlight_frames = sum(len(g) for g in valid_groups)
        raw_ratio = highlight_frames / sample_idx

        # Scale so ~25% highlight content -> ~1.0
        score = min(1.0, raw_ratio * 4.0)

        return round(score, 4)
