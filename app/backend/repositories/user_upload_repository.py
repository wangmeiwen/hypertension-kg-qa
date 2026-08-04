"""用户上传资源归属仓储。"""

from __future__ import annotations

from app.backend.repositories.database import execute


class UserUploadRepository:
    """持久化聊天上传文件与用户的归属关系。"""

    async def create_upload(
        self,
        *,
        upload_id: str,
        user_id: str,
        storage_filename: str,
        original_filename: str,
        provider: str,
        provider_file_id: str,
    ) -> None:
        await execute(
            """
            INSERT INTO user_uploads (
                id, user_id, storage_filename, original_filename,
                provider, provider_file_id
            ) VALUES (
                :id, :user_id, :storage_filename, :original_filename,
                :provider, :provider_file_id
            )
            """,
            {
                "id": upload_id,
                "user_id": user_id,
                "storage_filename": storage_filename,
                "original_filename": original_filename,
                "provider": provider,
                "provider_file_id": provider_file_id,
            },
        )
