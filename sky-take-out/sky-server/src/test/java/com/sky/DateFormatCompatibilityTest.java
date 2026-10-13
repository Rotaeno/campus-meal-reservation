package com.sky;

import com.sky.entity.MealSession;
import com.sky.json.JacksonObjectMapper;
import org.junit.jupiter.api.Test;
import java.time.LocalDateTime;
import static org.junit.jupiter.api.Assertions.*;

class DateFormatCompatibilityTest {
    private final JacksonObjectMapper mapper=new JacksonObjectMapper();
    @Test void acceptsExistingMinuteFormat() throws Exception {
        MealSession session=mapper.readValue("{\"cutoffTime\":\"2026-10-12 10:30\"}",MealSession.class);
        assertEquals(LocalDateTime.of(2026,10,12,10,30),session.getCutoffTime());
    }
    @Test void acceptsMealPickerSecondFormat() throws Exception {
        MealSession session=mapper.readValue("{\"cutoffTime\":\"2026-10-12 10:30:15\"}",MealSession.class);
        assertEquals(LocalDateTime.of(2026,10,12,10,30,15),session.getCutoffTime());
    }
    @Test void rejectsTrailingGarbage() {
        assertThrows(Exception.class,()->mapper.readValue("{\"cutoffTime\":\"2026-10-12 10:30bad\"}",MealSession.class));
    }
}
