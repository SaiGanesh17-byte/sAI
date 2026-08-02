// TO DO: Implement GET API logic here
package com.example;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class GetApiController {
    @GetMapping("/")
    public String home() {
        return "Hello World!";
    }
}