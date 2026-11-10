package com.petcare.data

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * 보호자 알림. 지금은 '밥이 오래 남아 있다' 하나다.
 *
 * 앱이 밥통 화면을 보고 있는 동안 판단한다 (백그라운드 작업은 없다).
 * 같은 남은 사료로 몇 번씩 울리지 않도록 화면 쪽에서 한 번만 부른다.
 */
object Notify {
    private const val CHANNEL = "leftover"
    private const val ID_LEFTOVER = 1001

    fun allowed(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED

    private fun ensureChannel(ctx: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "남은 사료", NotificationManager.IMPORTANCE_HIGH)
                    .apply { description = "밥그릇에 사료가 오래 남아 있을 때 알려줍니다" },
            )
        }
    }

    fun leftover(ctx: Context, dogName: String, grams: Double, minutes: Int) {
        if (!allowed(ctx)) return
        ensureChannel(ctx)
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("${dogName}가 밥을 남겼어요")
            .setContentText("사료 ${grams.toInt()}g이 ${minutes}분째 그대로예요. 식욕을 확인해 주세요.")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(ctx).notify(ID_LEFTOVER, n)
        } catch (_: SecurityException) {
            // 권한이 막 회수된 경우. 화면 배너는 그대로 보인다.
        }
    }
}
