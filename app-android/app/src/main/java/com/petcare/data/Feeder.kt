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
 * 밥통 제어. **경로가 둘이다.**
 *
 *     서버 경유   앱 -> 서버에 명령 적재 -> 밥통이 2초마다 상태를 보고하며 가져감
 *     직접        앱 -> ESP32 의 IP 로 바로 HTTP
 *
 * 평소에는 서버를 쓴다. 집 밖에서도 되고, 앱이 꺼져 있어도 급여가 나간다.
 *
 * 직접 경로는 **시연용으로 남겨둔다.** 버튼을 누르면 1초 안에 모터가 돌고,
 * 인터넷도 무료 호스팅의 콜드스타트도 끼지 않는다. 발표장에서 서버가 말을
 * 안 들을 때 쓸 보험이다. 같은 핫스팟에 있어야 한다.
 *
 * 직접 경로의 계약은 `firmware/esp32_dispenser/esp32_dispenser.ino` 와 같다.
 *
 *     GET  /status
 *     POST /dispense   slot=1..3 count=N  /  slot=4 g=N
 *     POST /stop
 *
 * 배출 요청은 바로 202 로 돌아오고 모터는 그 뒤에 돈다.
 * 진행 상황은 status 를 폴링해서 본다.
 */
object Feeder {

    /** 핫스팟에서 ESP32 가 받은 주소. 설정 화면에서 바꿀 수 있다. */
    const val DEFAULT_BASE = "http://172.20.10.8"

    const val VIA_SERVER = "server"
    const val VIA_DIRECT = "direct"

    /** 사료 슬롯. 서버와 같은 단위(그램)를 쓴다. */
    const val FOOD_SLOT = 4

    /**
     * 남은 사료 알림 기준. 그릇에 [LEFTOVER_G] 이상이 [LEFTOVER_MIN] 분 넘게
     * 그대로면 알린다. 안 먹는 것 자체가 식욕 저하 신호다.
     *
     * 시연에서는 1~2분으로 줄여서 보여준다.
     */
    const val LEFTOVER_G = 10.0
    const val LEFTOVER_MIN = 30.0

    /** 직접 경로에서 남은 사료가 시작된 시각. 서버 경로는 서버가 센다. */
    private var directLeftSince = 0L

    /**
     * 어디로 보낼지. 화면이 이걸 들고 다닌다.
     *
     * 서버 설정이 비어 있으면 [viaServer] 가 거짓이 되어 자동으로 직접
     * 경로로 떨어진다. 설정이 없는데 서버를 고르면 아무것도 안 되는 것보다
     * 돌아가는 쪽이 낫다.
     */
    data class Link(
        val route: String = VIA_SERVER,
        val direct: String = DEFAULT_BASE,
        val serverBase: String = "",
        val serverDogId: String = "",
        val serverToken: String = "",
    ) {
        val viaServer: Boolean
            get() = route == VIA_SERVER &&
                serverBase.isNotBlank() && serverDogId.isNotBlank()
    }

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
        /**
         * 밥통이 켜진 뒤 받은 털기 수 (-1 = 펌웨어가 안 보냄).
         *
         * 1분 요약은 1분이 지나야 바뀐다. 이 값은 털 때마다 바로 오른다.
         */
        val shakes: Int = -1,
        /**
         * 이 초를 넘으면 '지금 행동' 으로 안 보여준다.
         *
         * 직접 경로는 1초마다 읽으니 5초면 넉넉하다. 서버 경유는 밥통이
         * 60초마다 보고하므로 그 기준으로는 **항상 낡은 값**이 된다.
         * 경로마다 다른 값을 넣는다.
         */
        val staleAfter: Int = 5,
    ) {
        val behavior: String?
            get() = BEHAVIORS.getOrNull(cls)

        val fresh: Boolean get() = connected && cls >= 0 && age in 0..staleAfter

        companion object {
            val BEHAVIORS = listOf("쉬기", "걷기", "뛰기", "털기")

            /** 서버가 쓰는 이름. 펌웨어·서버 모델과 같은 순서다. */
            val IDS = listOf("rest", "walk", "run", "body_shake")
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
        /** 밥그릇 저울 g. 그릇 무게는 영점으로 뺐다. -1 = 저울 없음 */
        @kotlinx.serialization.SerialName("weight_g") val weightG: Double = -1.0,
        // --- 아래는 ESP32 가 보내지 않는다. 서버 경유일 때만 채운다 ---
        val via: String = VIA_DIRECT,
        /** 밥통이 마지막으로 보고한 뒤 지난 초. -1 = 모름 */
        val ageS: Int = -1,
        /** 아직 밥통이 안 가져간 명령 수. */
        val pending: Int = 0,
        /** 그릇에 기준 이상이 남은 채 지난 분. */
        val leftoverMin: Double = 0.0,
    ) {
        val hasScale: Boolean get() = weightG > -1.0
        val leftoverAlert: Boolean
            get() = hasScale && weightG >= LEFTOVER_G && leftoverMin >= LEFTOVER_MIN
    }

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

    // --- 직접 경로 ----------------------------------------------------------

    private suspend fun directStatus(base: String): Status =
        json.decodeFromString(call("${norm(base)}/status"))

    /** 한 끼 전체. pellets = 슬롯 -> 알 수, 사료는 [foodG] 그램. 직접 경로에서만 쓴다. */
    private suspend fun meal(base: String, pellets: Map<Int, Int>, foodG: Int) {
        val form = mutableMapOf<String, Any>()
        pellets.forEach { (slot, n) -> if (slot != FOOD_SLOT) form["s$slot"] = n }
        form["g"] = foodG
        call("${norm(base)}/meal", form)
    }

    // --- 서버 경유 ----------------------------------------------------------

    /**
     * 서버가 알려준 밥통 상태를 직접 경로와 같은 모양으로 옮긴다.
     *
     * 화면이 경로마다 다른 모델을 다루면 분기가 두 벌이 된다.
     */
    private fun fromServer(v: Net.FeederView): Status {
        val s = v.state
        val cls = Collar.IDS.indexOf(s?.collarClass ?: "")
        // 두 지연을 더해야 실제 나이가 된다 — 목줄이 판정한 뒤 밥통이
        // 보고하기까지, 그리고 밥통이 보고한 뒤 지금까지.
        val age = if (v.ageS == null) -1 else v.ageS + (s?.collarAgeS ?: 0)
        return Status(
            ok = v.online,
            busy = s?.busy == true,
            // 서버는 슬롯 정의를 모른다. 화면이 기본 목록으로 채운다.
            slots = emptyList(),
            collar = s?.let {
                Collar(
                    connected = it.collarConnected,
                    cls = cls,
                    conf = (it.collarConf * 100).toInt(),
                    age = age,
                    min = listOf(
                        it.minute.restS, it.minute.walkS,
                        it.minute.runS, it.minute.shakeN,
                    ),
                    minAge = age,
                    shakes = it.shakeLive,
                    // 밥통이 2초마다 보고한다. 한두 번 놓쳐도 낡았다고 하지 않는다.
                    staleAfter = 30,
                )
            },
            weightG = s?.weightG ?: -1.0,
            via = VIA_SERVER,
            ageS = age,
            pending = v.pending,
            leftoverMin = v.leftover?.minutes ?: 0.0,
        )
    }

    /**
     * 서버가 FastAPI 로 돌려주는 {"detail":"..."} 에서 사람이 읽을 부분만 뽑는다.
     * 못 뽑으면 원문을 그대로 쓴다 — 조용히 삼키면 왜 안 됐는지 알 수 없다.
     */
    private fun serverError(raw: String?): String {
        val text = raw ?: return "서버에 연결할 수 없습니다"
        val m = Regex(""""detail"\s*:\s*"((?:[^"\\]|\\.)*)"""").find(text)
        return m?.groupValues?.get(1)?.replace("\\\"", "\"") ?: text
    }

    // --- 바깥에서 쓰는 것 ---------------------------------------------------

    suspend fun status(link: Link): Status = try {
        if (link.viaServer) {
            fromServer(Net.feeder(link.serverBase, link.serverDogId, link.serverToken))
        } else {
            directStatus(link.direct).let { st ->
                val now = System.currentTimeMillis()
                if (st.weightG >= LEFTOVER_G) {
                    if (directLeftSince == 0L) directLeftSince = now
                } else {
                    directLeftSince = 0L
                }
                st.copy(
                    leftoverMin = if (directLeftSince == 0L) 0.0
                    else (now - directLeftSince) / 60_000.0,
                )
            }
        }
    } catch (e: Failed) {
        throw e
    } catch (e: Exception) {
        throw Failed(serverError(e.message))
    }

    /** 밥그릇 저울을 0g 으로. 그릇을 바꿨을 때 누른다. */
    suspend fun tare(link: Link) {
        try {
            if (link.viaServer) {
                Net.tareFeeder(link.serverBase, link.serverDogId, link.serverToken)
            } else {
                call("${norm(link.direct)}/tare", emptyMap())
            }
            directLeftSince = 0L
        } catch (e: Failed) {
            throw e
        } catch (e: Exception) {
            throw Failed(serverError(e.message))
        }
    }

    /**
     * 급여 계획 한 번에. 영양제 여러 슬롯 + 사료를 **명령 하나로** 보낸다.
     *
     * 슬롯마다 따로 보내면 서버가 두 번째부터 "대기 중인 명령이 있다" 고
     * 막는다 (이중 급여 방지). 계획은 원래 한 끼 단위이므로 한 덩어리가 맞다.
     */
    suspend fun dispensePlan(
        link: Link, pellets: Map<Int, Int>, foodG: Int, requestId: String = "",
    ) {
        try {
            if (link.viaServer) {
                Net.dispense(
                    link.serverBase, link.serverDogId,
                    pellets = pellets.filterKeys { it != FOOD_SLOT },
                    foodG = foodG.toDouble(),
                    requestId = requestId, token = link.serverToken,
                )
            } else {
                meal(link.direct, pellets, foodG)
            }
        } catch (e: Failed) {
            throw e
        } catch (e: Exception) {
            throw Failed(serverError(e.message))
        }
    }

    /**
     * 한 슬롯을 내보낸다.
     *
     * **사료(4번)의 [amount] 는 그램, 영양제는 알 수다.** 두 경로 모두
     * 같은 단위를 쓴다 - 경로마다 단위가 다르면 보호자가 틀린 양을 넣는
     * 날이 온다.
     */
    suspend fun dispense(link: Link, slot: Int, amount: Int, requestId: String = "") {
        try {
            if (link.viaServer) {
                if (slot == FOOD_SLOT) {
                    Net.dispense(
                        link.serverBase, link.serverDogId,
                        pellets = emptyMap(), foodG = amount.toDouble(),
                        requestId = requestId, token = link.serverToken,
                    )
                } else {
                    Net.dispense(
                        link.serverBase, link.serverDogId,
                        pellets = mapOf(slot to amount),
                        requestId = requestId, token = link.serverToken,
                    )
                }
            } else {
                val arg = if (slot == FOOD_SLOT) "g" else "count"
                call("${norm(link.direct)}/dispense", mapOf("slot" to slot, arg to amount))
            }
        } catch (e: Failed) {
            throw e
        } catch (e: Exception) {
            throw Failed(serverError(e.message))
        }
    }

    /**
     * 멈춘다. **경로마다 할 수 있는 일이 다르다.**
     *
     *     직접      진행 중인 배출을 다음 단위에서 멈춘다
     *     서버 경유 아직 안 가져간 명령을 취소한다 (도는 모터는 못 세운다)
     *
     * 돌아오는 문장이 그 차이를 말해준다. 화면이 "정지됐습니다" 라고
     * 뭉개면 보호자는 멈춘 줄 알고 통을 연다.
     */
    suspend fun stop(link: Link): String = try {
        if (link.viaServer) {
            Net.cancelDispense(link.serverBase, link.serverDogId, link.serverToken)
            "아직 안 가져간 명령을 취소했습니다"
        } else {
            call("${norm(link.direct)}/stop", emptyMap())
            "정지 요청을 보냈습니다"
        }
    } catch (e: Failed) {
        throw e
    } catch (e: Exception) {
        throw Failed(serverError(e.message))
    }
}

/** 밥통 주소 저장. 프로필과 섞지 않는다 — 기기 설정이지 개체 정보가 아니다. */
class FeederStore(context: Context) {
    private val prefs = context.getSharedPreferences("petcare.feeder", Context.MODE_PRIVATE)

    var base: String
        get() = prefs.getString("base", null) ?: Feeder.DEFAULT_BASE
        set(value) = prefs.edit().putString("base", value.trim()).apply()

    /**
     * 어느 경로로 보낼지. 기본은 서버다.
     *
     * 직접 경로는 같은 핫스팟에 있어야만 되므로 기본으로 둘 수 없다.
     * 발표장에서 필요할 때 화면에서 바꾼다.
     */
    var route: String
        get() = prefs.getString("route", null) ?: Feeder.VIA_SERVER
        set(value) = prefs.edit().putString("route", value).apply()
}
