package com.sky.reliability;

import java.lang.annotation.*;

/** Only annotate DB-only operations. The retry advice must surround the transaction advice. */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface RetryDatabaseTransaction { }
