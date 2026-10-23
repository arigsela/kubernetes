// The Anthropic Go SDK at the version kagent's Go ADK pins (v1.80.0), against the model gateway:
// a call from an over-budget group must fail at once with budget_exceeded, not sleep for the
// gateway's Retry-After (the seconds left in the day). Default client options, as the ADK uses.
package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/anthropics/anthropic-sdk-go"
	"github.com/anthropics/anthropic-sdk-go/option"
)

func main() {
	client := anthropic.NewClient(option.WithBaseURL(os.Getenv("BASE")), option.WithAPIKey(os.Getenv("KEY")))
	start := time.Now()
	_, err := client.Messages.New(context.Background(), anthropic.MessageNewParams{
		Model:     anthropic.Model(os.Getenv("MODEL")),
		MaxTokens: 16,
		Messages:  []anthropic.MessageParam{anthropic.NewUserMessage(anthropic.NewTextBlock("hi"))},
	})
	fmt.Printf("elapsed=%.1f\n", time.Since(start).Seconds())
	fmt.Printf("err=%v\n", err)
}
