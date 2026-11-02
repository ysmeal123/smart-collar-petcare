package com.petcare.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * 밥통(ESP32) 직접 제어.
 *
 * 서버를 거치지 않는다. 같은 핫스팟에 붙은 ESP32 에 HTTP 로 바로 명령한다.
 * 계약은 `firmware/esp32_dispenser/esp32_dispenser.ino` 의 HTTP 절과 같다.
 *
 *     GET  /status
 *     POST /dispense   slot=1..4 count=N
 *     POST /meal       s1..s4
 *     POST /stop
 *
 * 배출 요청은 바로 202 로 돌아오고 모터는 그 뒤에 돈다.
 * 진행 상황은 status 를 폴링해서 본다.
 */
object Feeder {

    /** 핫스팟에서 ESP32 가 받은 주소. 설정 화면에서 바꿀 수 있다. */
    const val DEFAULT_BASE = "http://172.20.10.8"

    // 같은 핫스팟 안이라 오래 기다릴 이유가 없다. 늦으면 끊긴 것이다.
    private const val CONNECT_MS = 3_000
    private const val READ_MS = 4_000

    private val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
    }

    class Failed(message: String) : Exception(message)

    @Serializable
    data class Slot(
        val slot: Int,
        val id: String = "",
        val name: String = "",
        val unit: String = "",
        val max: Int = 10,
        val requested: Int = 0,
        val done: Int = 0,
    )

    /**
     * 밥통이 BLE 로 받은 목줄(XIAO) 상태.
     *
     * cls: 0 쉬기 / 1 걷기 / 2 뛰기 / 3 털기, -1 = 아직 없음
     * age: 마지막 판정 뒤 지난 초 (-1 = 받은 적 없음)
     * min: 최근 1분 [쉬기초, 걷기초, 뛰기초, 털기회]
     */
    @Serializable
    data class Collar(
        val connected: Boolean = false,
        val cls: Int = -1,
        val conf: Int = 0,
        val age: Int = -1,
        val min: List<Int> = emptyList(),
        @kotlinx.serialization.SerialName("min_age") val minAge: Int = -1,
    ) {
        val behavior: String?
            get() = BEHAVIORS.getOrNull(cls)

        /** 판정이 5초 넘게 안 오면 지금 행동으로 보여주지 않는다. */
        val fresh: Boolean get() = connected && cls >= 0 && age in 0..5

        companion object {
            val BEHAVIORS = listOf("쉬기", "걷기", "뛰기", "털기")
        }
    }

    @Serializable
    data class Status(
        val ok: Boolean = false,
        val busy: Boolean = false,
        val job: Long = 0,
        val ip: String = "",
        val rssi: Int = 0,
        val stopped: Boolean = false,
        val slots: List<Slot> = emptyList(),
        val collar: Collar? = null,
    )

    @Serializable
    private data class Reply(val ok: Boolean = false, val error: String = "")

    private suspend fun call(
        url: String,
        form: Map<String, Any>? = null,
    ): String = withContext(Dispatchers.IO) {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = if (form != null) "POST" else "GET"
            connectTimeout = CONNECT_MS
            readTimeout = READ_MS
            setRequestProperty("Accept", "application/json")
            if (form != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
            }
        }
        try {
            if (form != null) {
                val body = form.entries.joinToString("&") { (k, v) ->
                    "${enc(k)}=${enc(v.toString())}"
                }
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
            if (code !in 200..299) {
                val err = runCatching { json.decodeFromString<Reply>(text).error }.getOrNull()
                throw Failed(humanize(code, err))
            }
            text
        } catch (e: Failed) {
            throw e
        } catch (e: Exception) {
            throw Failed("밥통에 연결할 수 없습니다 (${e.javaClass.simpleName})")
        } finally {
            conn.disconnect()
        }
    }

    private fun humanize(code: Int, err: String?): String = when {
        code == 409 -> "다른 배출이 진행 중입니다"
        err.isNullOrBlank() -> "HTTP $code"
        else -> err
    }

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")

    private fun norm(base: String) = base.trim().trimEnd('/').let {
        if (it.startsWith("http://") || it.startsWith("https://")) it else "http://$it"
    }

    suspend fun status(base: String): Status =
        json.decodeFromString(call("${norm(base)}/status"))

    suspend fun dispense(base: String, slot: Int, count: Int) {
        call("${norm(base)}/dispense", mapOf("slot" to slot, "count" to count))
    }

    /** 한 끼 전체. counts[0] = 슬롯 1. */
    suspend fun meal(base: String, counts: List<Int>) {
        val form = counts.mapIndexed { i, c -> "s${i + 1}" to (c as Any) }.toMap()
        call("${norm(base)}/meal", form)
    }

    suspend fun stop(base: String) {
        call("${norm(base)}/stop", emptyMap())
    }
}

/** 밥통 주소 저장. 프로필과 섞지 않는다 — 기기 설정이지 개체 정보가 아니다. */
class FeederStore(context: Context) {
    private val prefs = context.getSharedPreferences("petcare.feeder", Context.MODE_PRIVATE)

    var base: String
        get() = prefs.getString("base", null) ?: Feeder.DEFAULT_BASE
        set(value) = prefs.edit().putString("base", value.trim()).apply()
}
