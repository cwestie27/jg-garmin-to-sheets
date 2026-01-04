from datetime import date, datetime
from typing import Dict, Any, Optional
import asyncio
import logging
import os
import garminconnect
from garth.sso import resume_login
import garth
from .exceptions import MFARequiredException
from .config import GarminMetrics

logger = logging.getLogger(__name__)

class GarminClient:
    def __init__(self, email: str, password: str):
        self.client = garminconnect.Garmin(email, password)
        self._authenticated = False
        self.mfa_ticket_dict = None
        self._auth_failed = False  # Track if authentication failed to prevent loops

    async def authenticate(self):
        """Modified to handle non-async login method and load tokens if available."""
        # Store the garth client instance before attempting login
        initial_garth_client = self.client.garth

        try:
            def login_wrapper():
                # --- START OF FIX: Load Tokens if available ---
                token_secret = os.getenv("GARMIN_TOKENS")
                if token_secret:
                    logger.info("Found GARMIN_TOKENS secret. Attempting to load session...")
                    try:
                        self.client.garth.loads(token_secret)
                        logger.info("Session tokens loaded successfully!")
                        
                        # --- NEW FIX FOR 403 ERROR ---
                        # We must manually fetch the username because we skipped the standard login
                        self.client.display_name = self.client.garth.profile.get("displayName")
                        self.client.full_name = self.client.garth.profile.get("fullName")
                        logger.info(f"Session hydrated for user: {self.client.display_name}")
                        # -----------------------------

                        return True
                    except Exception as e:
                        logger.error(f"Failed to load tokens: {e}")
                # --- END OF FIX ---

                return self.client.login()
            
            login_result = await asyncio.get_event_loop().run_in_executor(None, login_wrapper)
            
            # If login_wrapper completes without raising an exception, it's a successful login.
            self._authenticated = True
            self.mfa_ticket_dict = None

        except AttributeError as e:
            if "'dict' object has no attribute 'expired'" in str(e):
                logger.info("Caught AttributeError indicating MFA challenge.")
                if hasattr(self.client.garth, 'oauth2_token') and isinstance(self.client.garth.oauth2_token, dict):
                    self.mfa_ticket_dict = self.client.garth.oauth2_token
                    logger.info(f"MFA ticket (dict) captured: {self.mfa_ticket_dict}")
                    raise MFARequiredException(message="MFA code is required.", mfa_data=self.mfa_ticket_dict)
                else:
                    logger.error("MFA detected via AttributeError, but self.client.garth.oauth2_token is not a dict.")
                    raise
            else:
                raise
        except garminconnect.GarminConnectAuthenticationError as e:
            if "MFA-required" in str(e) or "Authentication failed" in str(e):
                logger.info("Caught GarminConnectAuthenticationError indicating MFA challenge.")
                if hasattr(self.client.garth, 'oauth2_token') and isinstance(self.client.garth.oauth2_token, dict):
                    self.mfa_ticket_dict = self.client.garth.oauth2_token
                    logger.info(f"MFA ticket (dict) captured: {self.mfa_ticket_dict}")
                    raise MFARequiredException(message="MFA code is required.", mfa_data=self.mfa_ticket_dict)
                else:
                    logger.error("MFA detected via GarminConnectAuthenticationError, but self.client.garth.oauth2_token is not a dict.")
                    raise
            else:
                raise
        except Exception as e:
            logger.error(f"An unexpected error occurred during authentication: {str(e)}")
            raise garminconnect.GarminConnectAuthenticationError(f"An unexpected error occurred during authentication: {str(e)}") from e

    async def _fetch_hrv_data(self, target_date_iso: str) -> Optional[Dict[str, Any]]:
        """Fetches HRV data for the given date."""
        try:
            hrv_data = await asyncio.get_event_loop().run_in_executor(
                None, self.client.get_hrv_data, target_date_iso
            )
            logger.debug(f"Raw HRV data for {target_date_iso}: {hrv_data}")
            return hrv_data
        except Exception as e:
            logger.error(f"Error fetching HRV data for {target_date_iso}: {str(e)}")
            return None

    async def get_metrics(self, target_date: date) -> GarminMetrics:
        logger.debug(f"VERIFY get_metrics: display_name: {getattr(self.client, 'display_name', 'Not Set')}")
        if not self._authenticated:
            if self._auth_failed:
                raise Exception("Authentication has already failed. Cannot fetch metrics.")
            await self.authenticate()

        try:
            # Create fetch tasks
            async def get_stats():
                return await asyncio.get_event_loop().run_in_executor(
                    None, self.client.get_stats_and_body, target_date.isoformat()
                )

            async def get_sleep():
                return await asyncio.get_event_loop().run_in_executor(
                    None, self.client.get_sleep_data, target_date.isoformat()
                )

            async def get_activities():
                return await asyncio.get_event_loop().run_in_executor(
                    None, self.client.get_activities_by_date, 
                    target_date.isoformat(), target_date.isoformat()
                )

            async def get_user_summary():
                return await asyncio.get_event_loop().run_in_executor(
                    None, self.client.get_user_summary, target_date.isoformat()
                )

            async def get_training_status():
                return await asyncio.get_event_loop().run_in_executor(
                    None, self.client.get_training_status, target_date.isoformat()
                )
            
            async def get_hrv():
                return await self._fetch_hrv_data(target_date.isoformat())

            # Execute concurrently
            stats, sleep_data, activities, summary, training_status, hrv_payload = await asyncio.gather(
                get_stats(), get_sleep(), get_activities(), get_user_summary(), get_training_status(), get_hrv()
            )

            # Process HRV
            overnight_hrv_value: Optional[int] = None
            hrv_status_value: Optional[str] = None
            if hrv_payload:
                hrv_summary = hrv_payload.get('hrvSummary')
                if hrv_summary:
                    overnight_hrv_value = hrv_summary.get('lastNightAvg')
                    hrv_status_value = hrv_summary.get('status')
                else:
                    logger.warning(f"hrvSummary not found in hrv_payload for {target_date}")
            else:
                logger.warning(f"hrv_payload for {target_date} is None")

            # Process activities
            running_count = 0
            running_distance = 0
            cycling_count = 0
            cycling_distance = 0
            strength_count = 0
            strength_duration = 0
            cardio_count = 0
            cardio_duration = 0
            tennis_count = 0
            tennis_duration = 0

            if activities:
                for activity in activities:
                    activity_type = activity.get('activityType', {})
                    type_key = activity_type.get('typeKey', '').lower()
                    parent_type_id = activity_type.get('parentTypeId')

                    if 'run' in type_key or parent_type_id == 1:
                        running_count += 1
                        running_distance += activity.get('distance', 0) / 1000
                    elif 'virtual_ride' in type_key or 'cycling' in type_key or parent_type_id == 2:
                        cycling_count += 1
                        cycling_distance += activity.get('distance', 0) / 1000
                    elif 'strength' in type_key:
                        strength_count += 1
                        strength_duration += activity.get('duration', 0) / 60
                    elif 'cardio' in type_key:
                        cardio_count += 1
                        cardio_duration += activity.get('duration', 0) / 60
                    elif 'tennis' in type_key:
                        tennis_count += 1
                        tennis_duration += activity.get('duration', 0) / 60
            else:
                logger.warning(f"Activities data for {target_date} is None")

            # Initialize metrics
            sleep_score: Optional[float] = None
            sleep_length: Optional[float] = None
            weight: Optional[float] = None
            body_fat: Optional[float] = None
            blood_pressure_systolic: Optional[int] = None
            blood_pressure_diastolic: Optional[int] = None
            active_calories: Optional[int] = None
            resting_calories: Optional[int] = None
            intensity_minutes: Optional[int] = None
            resting_heart_rate: Optional[int] = None
            average_stress: Optional[int] = None
            vo2max_running: Optional[float] = None
            vo2max_cycling: Optional[float] = None
            training_status_phrase: Optional[str] = None
            steps: Optional[int] = None

            # Process sleep
            if sleep_data:
                sleep_dto = sleep_data.get('dailySleepDTO', {})
                if sleep_dto:
                    sleep_score = sleep_dto.get('sleepScores', {}).get('overall', {}).get('value')
                    sleep_time_seconds = sleep_dto.get('sleepTimeSeconds')
                    if sleep_time_seconds is not None and sleep_time_seconds > 0:
                        sleep_length = sleep_time_seconds / 3600
                    # --- NEW: Extract and Convert Start/End Times ---
                    start_unix = sleep_dto.get('sleepStartTimestampGMT')
                    end_unix = sleep_dto.get('sleepEndTimestampGMT')

                    if start_unix:
                        # Converts ms to seconds and then to local time string
                        sleep_start = datetime.fromtimestamp(start_unix / 1000).strftime('%H:%M:%S')
                    if end_unix:
                        sleep_end = datetime.fromtimestamp(end_unix / 1000).strftime('%H:%M:%S')
                else:
                    logger.warning(f"Daily sleep DTO not found for {target_date}")
            else:
                logger.warning(f"Sleep data for {target_date} is None")

            # Stats (Weight/Body Fat/BP)
            if stats:
                weight = stats.get('weight', 0) / 1000 if stats.get('weight') else None
                body_fat = stats.get('bodyFat')
                blood_pressure_systolic = stats.get('systolic')
                blood_pressure_diastolic = stats.get('diastolic')
            else:
                logger.warning(f"Stats data for {target_date} is None")

            # Summary
            if summary:
                active_calories = summary.get('activeKilocalories')
                resting_calories = summary.get('bmrKilocalories')
                intensity_minutes = (summary.get('moderateIntensityMinutes', 0) or 0) + (2 * (summary.get('vigorousIntensityMinutes', 0) or 0))
                resting_heart_rate = summary.get('restingHeartRate')
                average_stress = summary.get('averageStressLevel')
                steps = summary.get('totalSteps')
            else:
                logger.warning(f"User summary data for {target_date} is None")

            # Training Status / VO2 Max
            if training_status:
                most_recent_vo2max = training_status.get('mostRecentVO2Max')
                if most_recent_vo2max:
                    generic_vo2max = most_recent_vo2max.get('generic')
                    if generic_vo2max:
                        vo2max_running = generic_vo2max.get('vo2MaxValue')
                    cycling_vo2max = most_recent_vo2max.get('cycling')
                    if cycling_vo2max:
                        vo2max_cycling = cycling_vo2max.get('vo2MaxValue')

                training_status_data = {}
                most_recent_training_status = training_status.get('mostRecentTrainingStatus')
                if most_recent_training_status:
                    latest = most_recent_training_status.get('latestTrainingStatusData')
                    if latest:
                        training_status_data = latest
                
                # Get first available device status
                first_device = next(iter(training_status_data.values()), None) if training_status_data else None
                if first_device:
                    training_status_phrase = first_device.get('trainingStatusFeedbackPhrase')
            else:
                logger.warning(f"Training status data for {target_date} is None")

            return GarminMetrics(
                date=target_date,
                sleep_score=sleep_score,
                sleep_length=sleep_length,
                sleep_start=sleep_start,  
                sleep_end=sleep_end,
                weight=weight,
                body_fat=body_fat,
                blood_pressure_systolic=blood_pressure_systolic,
                blood_pressure_diastolic=blood_pressure_diastolic,
                active_calories=active_calories,
                resting_calories=resting_calories,
                resting_heart_rate=resting_heart_rate,
                average_stress=average_stress,
                training_status=training_status_phrase,
                vo2max_running=vo2max_running,
                vo2max_cycling=vo2max_cycling,
                intensity_minutes=intensity_minutes,
                all_activity_count=len(activities) if activities is not None else 0,
                running_activity_count=running_count,
                running_distance=running_distance,
                cycling_activity_count=cycling_count,
                cycling_distance=cycling_distance,
                strength_activity_count=strength_count,
                strength_duration=strength_duration,
                cardio_activity_count=cardio_count,
                cardio_duration=cardio_duration,
                tennis_activity_count=tennis_count,
                tennis_activity_duration=tennis_duration,
                overnight_hrv=overnight_hrv_value,
                hrv_status=hrv_status_value,
                steps=steps
            )

        except Exception as e:
            logger.error(f"Error fetching metrics for {target_date}: {str(e)}")
            return GarminMetrics(
                date=target_date,
                overnight_hrv=locals().get('overnight_hrv_value'),
                hrv_status=locals().get('hrv_status_value')
            )

    async def submit_mfa_code(self, mfa_code: str):
        """Submits the MFA code to complete authentication."""
        if not hasattr(self, 'mfa_ticket_dict') or not self.mfa_ticket_dict:
            raise Exception("MFA ticket not available. Please authenticate first.")

        try:
            loop = asyncio.get_event_loop()
            resume_login_result = await loop.run_in_executor(
                None,
                lambda: resume_login(self.mfa_ticket_dict, mfa_code)
            )

            if isinstance(resume_login_result, tuple) and len(resume_login_result) == 2:
                oauth1_token, oauth2_token = resume_login_result
            else:
                raise Exception("MFA token processing failed: Unexpected result from resume_login.")

            if 'client' in self.mfa_ticket_dict and isinstance(self.mfa_ticket_dict.get('client'), garth.Client):
                garth_client_instance = self.mfa_ticket_dict['client']
                garth_client_instance.oauth1_token = oauth1_token
                garth_client_instance.oauth2_token = oauth2_token
                self.client.garth = garth_client_instance

                # Attempt to fetch profile details
                try:
                    profile_data = self.client.garth.profile
                    if profile_data:
                        self.client.display_name = profile_data.get("displayName")
                        self.client.full_name = profile_data.get("fullName")
                        self.client.unit_system = profile_data.get("measurementSystem")
                        logger.info(f"Profile Loaded: {self.client.display_name}")
                    else:
                        logger.warning("Profile data was empty after MFA.")
                except Exception as e_profile:
                    logger.error(f"Failed to load profile after MFA: {e_profile}")
                    # We continue anyway because we are authenticated
            else:
                raise Exception("Critical error: Could not retrieve garth.Client instance.")
            
            self._authenticated = True
            self.mfa_ticket_dict = None
            return True

        except Exception as e:
            self._authenticated = False
            self._auth_failed = True
            error_msg = str(e)
            logger.error(f"MFA submission failed: {error_msg}")
            
            if "429" in error_msg:
                raise Exception("Garmin is rate limiting your requests. Wait 5-10 mins.")
            else:
                raise Exception(f"MFA failed: {error_msg}")
