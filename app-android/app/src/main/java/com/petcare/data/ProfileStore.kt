package com.petcare.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/**
 * 온보딩 결과를 기기에 저장한다.
 *
 * 상용에서는 DataStore + 서버 동기화가 맞다. 여기서는 의존성을 늘리지 않으려고
 * SharedPreferences 에 JSON 한 줄로 넣는다. 저장 포맷이 서버 스키마와 같아서
 * 나중에 그대로 POST 로 옮길 수 있다.
 */
class ProfileStore(context: Context) {

    private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
    }

    suspend fun load(): MyDog? = withContext(Dispatchers.IO) {
        val raw = prefs.getString(KEY_DOG, null) ?: return@withContext null
        // 앱을 업데이트하며 스키마가 바뀌면 파싱이 깨질 수 있다.
        // 그때 크래시 대신 온보딩을 다시 태우는 쪽이 낫다.
        runCatching { json.decodeFromString<MyDog>(raw) }.getOrNull()
    }

    suspend fun save(dog: MyDog) = withContext(Dispatchers.IO) {
        prefs.edit()
            .putString(KEY_DOG, json.encodeToString(dog))
            .apply()
    }

    suspend fun clear() = withContext(Dispatchers.IO) {
        prefs.edit().remove(KEY_DOG).apply()
    }

    /** 서버로 보낼 때 쓸 JSON. 백엔드 DogProfile 스키마와 같다. */
    fun toJson(dog: MyDog): String = json.encodeToString(dog)

    private companion object {
        const val PREFS = "petcare.profile"
        const val KEY_DOG = "my_dog"
    }
}
